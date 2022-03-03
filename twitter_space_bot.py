import sys
import time
import tweepy
import xhr_grabber
import requests
import twspace
import threading
import re
import subprocess
import const
from datetime import datetime
from log import create_logger
import json


SLEEP_TIME = const.SLEEP_TIME
api_key = const.api_key
api_key_secret = const.api_key_secret
bearer_token = const.bearer_token
access_token = const.access_token
access_token_secret = const.access_token_secret
WEBHOOK_URL = const.WEBHOOK_URL
DOWNLOAD = const.DOWNLOAD

# Authorize and setup twitter client
auth = tweepy.OAuthHandler(api_key, api_key_secret)
auth.set_access_token(access_token, access_token_secret)
api = tweepy.API(auth)
twitter_client = tweepy.Client(bearer_token, consumer_key=api_key, consumer_secret=api_key_secret,
                               access_token=access_token, access_token_secret=access_token_secret)

# List of twitter creators to monitor
twitter_ids = const.twitter_ids

space_fields = ['id', 'state', 'title', 'started_at']
user_fields = ['profile_image_url']
expansions = ['creator_id', 'host_ids']

twitter_id_list = []
for twitter_user in twitter_ids:
    twitter_id_list.append(str(*twitter_user.values()))

user_ids = ",".join(twitter_id_list)


def get_m3u8_id(url):
    return re.search("(.*\/Transcoding\/v1\/hls\/(.*)(\/non_transcode.*))", url).group(2)


# return a tuple of (deployment server, periscope server) where
# deployment server can be either prod-fastly or canary-video while a periscope server can be ap-northeast-1.video or us-east-1
def get_server(url):
    reg_result = re.search("(https:\/\/)((?:[^-]*-){2})(.*)(\.pscp.*)", url)
    # regex will return something like 'prod-fastly-' so remove the last dash
    deployment_server = reg_result.group(2)[:-1]
    periscope_server = reg_result.group(3)
    server = (deployment_server, periscope_server)
    return server


def get_spaces():
    # TODO catch specific errors such as 429 too many requests and put the program to sleep
    try:
        # for some darn reason space_fields do not work
        req = twitter_client.get_spaces(expansions=expansions, user_ids=twitter_id_list, space_fields=space_fields, user_fields=user_fields)
    except Exception as e:
        logger.error(e, exc_info=True)
        return None
    # response example with two difference spaces
    # Response(data=[<Space id=1vOGwyQpQAVxB state=live>, <Space id=1ypKdEePLXLGW state=live>], includes={'users': [<User id=838403636015185920 name=Misaネキ username=Misamisatotomi>, <User id=1181889913517572096 name=アステル・レダ🎭 / オリジナルソングMV公開中!! username=astelleda>]}, errors=[], meta={'result_count': 2})
    spaces = []
    result_count = req[3]["result_count"]
    if result_count != 0:
        datas = req[0]
        users = req[1]["users"]
        for data, user in zip(datas, users):
            spaces.append([data, user])
    return spaces


def download(notified_space):
    if DOWNLOAD is not None or False:
        notified_space_id = notified_space[0]["id"]
        notified_space_creator = notified_space[1]
        if notified_space[0] is not None:
            notified_space_started_at = notified_space[0].started_at.strftime("%Y%m%d")
        else:
            notified_space_started_at = datetime.utcnow().strftime("%Y%m%d")
        notified_space_title = notified_space[0].title
        # Use default space title if it's not supplied
        if notified_space_title is None:
            notified_space_title = f"{notified_space_creator} space"
        notified_space_m3u8_id = get_m3u8_id(notified_space[2])
        notified_space_periscope_server = get_server(notified_space[2])
        logger.info(f"Starting download since {notified_space_creator} is now offline at {notified_space_id}")
        threading.Thread(target=twspace.download,
                         args=[notified_space_m3u8_id, notified_space_id, notified_space_creator,
                               notified_space_title, notified_space_started_at, notified_space_periscope_server]).start()


def check_status(notified_spaces, space_list):
    if len(notified_spaces) != 0:
        for notified_space in notified_spaces:
            counter = 0
            # If no more spaces are found then automatically download
            if len(space_list) == 0:
                try:
                    download(notified_space)
                except Exception as e:
                    logger.error("Error, aborting download, please download manually")
                    logger.error(e, exc_info=True)
                notified_spaces.remove(notified_space)
            # Check if a space went offline to download
            for space in space_list:
                if len(space_list) == 0 or counter == len(space_list) and notified_space[0]["id"] != space[0]["id"]:
                    try:
                        download(notified_space)
                    except Exception as e:
                        logger.error("Error, aborting download, please download manually")
                        logger.error(e, exc_info=True)
                        continue
                    notified_spaces.remove(notified_space)
                counter += 1


if __name__ == "__main__":
    logger = create_logger("logfile.log")
    notified_spaces = []
    logger.info("Starting program")

    while True:
        try:
            space_list = get_spaces()
            # If there was an error then continue the loop
            if space_list is None:
                continue
            check_status(notified_spaces, space_list)

            # Get and send out space url and m3u8 to discord webhook
            for space in space_list:
                logger.debug(space)
                logger.debug(space[0]['data'])
                logger.debug(space[1]['data'])
                if len(space_list) != 0:
                    # Ignore if the space is scheduled to be live
                    if space[0]['state'] == 'scheduled':
                        continue
                    space_id = space[0]["id"]
                    if not any(space_id == notified_space[0]["id"] for notified_space in notified_spaces):
                        status = space[0]["state"]
                        creator_profile_image = space[1].profile_image_url
                        space_creator = space[1]
                        space_started_at = space[0].started_at.strftime("%Y%m%d")
                        space_title = space[0].title
                        # If no space title has been set then go with the default
                        if space_title is None:
                            space_title = "Twitter Space"
                        space_url = f"https://twitter.com/i/spaces/{space_id}"

                        # Get and send the m3u8 url
                        m3u8_url = xhr_grabber.get_m3u8(space_url)
                        if m3u8_url is not None:
                            logger.info(f"{space_creator} is now {status} at {space_url}")
                            logger.info(f"M3U8: {m3u8_url}")
                            message = {"embeds": [{
                                "color": 1942002,
                                "author": {
                                    "name": f"{space_creator}",
                                    "icon_url": creator_profile_image
                                },
                                "fields": [
                                    {
                                        "name": space_title,
                                        "value": f"{space_creator} is now {status} at [{space_url}]({space_url}) ```{m3u8_url}```"
                                    }
                                ],
                                "thumbnail": {
                                    "url": creator_profile_image.replace("normal", "200x200")
                                }
                            }]
                            }
                            if WEBHOOK_URL is not None:
                                requests.post(WEBHOOK_URL, json=message)
                            m3u8_id = m3u8_url
                            notified_space = space
                            notified_space.append(m3u8_id)
                            notified_spaces.append(notified_space)
            logger.info(f"Sleeping for {SLEEP_TIME} secs...")
            time.sleep(SLEEP_TIME)
        except SystemExit:
            sys.exit()
        except Exception as e:
            logger.error(e, exc_info=True)
