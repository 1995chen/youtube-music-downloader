import os
import sys
import logging
import copy
import glob
import re
import shutil
from typing import Union
from urllib.parse import urlparse, parse_qs

import requests
import itunespy
import yt_dlp
import ffmpeg
from ytmusicapi import YTMusic
from mutagen.id3 import (
    ID3,
    APIC,
    TIT2,
    TPE1,
    TALB,
    TCON,
    TRCK,
    TYER,
)
from mutagen.mp3 import MP3
from colorama import Fore, Style
from downloader_cli.download import Download

from meta import (
    gaana,
    deezer,
    saavn,
    lastfm,
    musicbrainz,
    spotify,
)

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    '[%(process)d-%(thread)d]-[%(levelno)s] %(asctime)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 使用StreamHandler输出到控制台
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# 添加两个Handler
root_logger.addHandler(console_handler)
logger = logging.getLogger(__name__)

play_list = "https://music.youtube.com/playlist?list=PLZAE9aF6H86HZUSFf30crtLm22UiaoVrX"
dest_dir = "C:/tmp"

ydl_opts = {
    "quiet": True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'source_address': '0.0.0.0',
    # 'format': 'bestaudio/best',
    # 'dump_single_json': True,
    # 'extract_flat': True,
}
TMP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tmp')
COVER_IMG = os.path.join(TMP_PATH, 'cover.jpg')


def get_playlist(url, ):
    """
    Extract the playlist data and return it accordingly.

    The return result will be a dictionary with the following
    entries
    url  : URL of the video
    """
    ydl_opts_cp = copy.deepcopy(ydl_opts)
    ydl_opts_cp.update({
        'format': 'bestaudio/best',
        'dump_single_json': True,
        'extract_flat': True,
    })
    # Extract the info now
    songs = yt_dlp.YoutubeDL(ydl_opts_cp).extract_info(url, False)
    return songs["entries"], songs["title"]


def remove_yt_words(title):
    """
    Remove words like Official video etc from the name of the song
    """
    # Remove stopwords like official, video etc
    # Remove square as well as circle brackets
    # Remove dashes (-) and trademark icons like ®
    # Remove spaces in the beginning or at the end
    title = re.sub(
        r'\]|\[|official|video|music|audio|full|lyrics?|-|\)|\(|®|^[ ]*|[ ]*$',
        '',
        str(title).lower()
    )
    # Replace more than one space with one space
    title = re.sub(r'\ {2,}', ' ', title)
    return title


def get_title(url) -> str:
    """
    Try to get the title of the song.

    This is mostly used when URL is passed or playlist
    links are passed since in those cases the title is
    not explicitly passed.
    """
    # Primarily try to get the title by using Youtube
    # Music.
    try:
        videoId = parse_qs(urlparse(url=url).query)["v"][0]
        ytmusic = YTMusic()

        details = ytmusic.get_song(videoId=videoId)

        # Check if error occured
        if details["playabilityStatus"]["status"] != "OK":
            raise Exception(videoId)
        return details["videoDetails"]["title"]
    except Exception:
        logger.debug(f"YtMusic wasn't able to find title for {url}")

    ydl_opts_cp = copy.deepcopy(ydl_opts)
    # Try Youtube as a fallback
    ydl = yt_dlp.YoutubeDL(ydl_opts_cp)
    data = ydl.extract_info(url, False)
    return remove_yt_words(data["title"])


# Function to be called by ytdl progress hook.
def progress_handler(d):
    d_obj = Download('', '')

    if d['status'] == 'downloading':
        try:
            length = d_obj._get_terminal_length()
        except Exception:
            length = 120
        time_left = d['eta']
        f_size_disp, dw_unit = d_obj._format_size(d['downloaded_bytes'])

        # Total bytes might not be always passed, sometimes
        # total_bytes_estimate is passed
        try:
            total_bytes = d['total_bytes']
        except KeyError:
            total_bytes = d['total_bytes_estimate']

        percent = d['downloaded_bytes'] / total_bytes * 100
        speed, s_unit, time_left, time_unit = d_obj._get_speed_n_time(
            d['downloaded_bytes'],
            0,
            cur_time=d['elapsed'] - 6
        )

        status = r"%-7s" % ("%s %s" % (round(f_size_disp), dw_unit))
        if d['speed'] is not None:
            speed, s_unit = d_obj._format_speed(d['speed'] / 1000)
            status += r"| %-3s " % ("%s %s" % (round(speed), s_unit))

        status += r"|| ETA: %-4s " % (
                "%s %s" %
                (round(time_left), time_unit))

        status = d_obj._get_bar(status, length, percent)
        status += r" %-4s" % ("{}%".format(round(percent)))

        sys.stdout.write('\r')
        sys.stdout.write(status)
        sys.stdout.flush()


def search(url) -> Union[str, str]:
    """Search the song on YouTube, ask the user for an input and accordingly
    return a selected song.

    The song can be extracted either from an URL or the name. If the name is
    present, we will search on YouTube for the name, ask the user for a choice
    and accordingly return the choice.

    If the URL is present, we will extract the data from the URL and return it
    accordingly.
    """
    # If the url is passed then get the data
    data = []

    # Strip unwanted stuff from the URL
    url = url.split("&")[0]
    queries = parse_qs(urlparse(url=url).query)
    href_url = f"/watch?v={queries['v'][0]}"
    # Get video data from youtube
    proxies = {}
    search_tmplt = "https://www.youtube.com/oembed?url={}&format=json"
    search_url = search_tmplt.format(href_url)
    r = requests.get(search_url, proxies=proxies)
    if r.status_code == 200:
        temp_data = r.json()
    else:
        raise Exception("Unauthorized")

    # Sometimes the temp_data may be returned as unauthorized, skip that
    if type(temp_data) is str and temp_data.lower() == "Unauthorized".lower():
        raise Exception(f"{url}: is unauthorized")

    data.append(temp_data)

    # In this case choice will be 0
    return url, str(data[0]["title"])


def download(link, song_name) -> str:
    """Download the song by using the passed link.

    The song will be saved with the passed title.
    Return the saved path of the song.
    """
    # If song_name doesn't have mp3 extension, add it
    if not song_name.endswith("mp3"):
        song_name += '.' + "mp3"

    # Replace the spaces with hashes
    song_name = re.sub(r" |/", "#", song_name)

    # The directory where we will download to.
    dw_dir = os.path.join(TMP_PATH, 'music')
    logger.info("Saving the files to: {}".format(dw_dir))

    if not os.path.exists(dw_dir):
        os.makedirs(dw_dir)

    # Name of the temp file
    _path = os.path.join(dw_dir, song_name)
    logger.debug(_path)

    # Start downloading the song
    ydl_opts_cp = copy.deepcopy(ydl_opts)
    ydl_opts_cp['outtmpl'] = _path
    ydl_opts_cp['format'] = 'bestaudio/best'
    ydl_opts_cp['progress_hooks'] = [progress_handler]
    ydl = yt_dlp.YoutubeDL(ydl_opts_cp)
    ydl.download([link])
    logger.info('Downloaded!')
    return _path


def convert_to_mp3(path):
    """Covert to mp3 using the python ffmpeg module."""
    new_name = path[:-4] + '_new.mp3'
    params = {
        "loglevel": "panic",
        "ar": 44100,
        "ac": 2,
        "ab": '320k',
        "f": "mp3"
    }

    try:
        job = ffmpeg.input(path).output(
            new_name,
            **params
        )
        job.run()
        os.remove(path)
        return new_name
    except ffmpeg._run.Error:
        # This error is usually thrown where ffmpeg doesn't have to
        # overwrite a file.
        # The bug is from ffmpeg, I'm just adding this catch to
        # handle that.
        return new_name


def dwCover(song):
    """Download the song cover img from itunes."""
    # Try to download the cover art as cover.jpg in temp
    logger.info("Preparing the album cover")
    try:
        imgURL = song.artwork_url_100

        # Check if the passed imgURL is a local file
        # this is possible if the metadata was entered manually.
        imgURL = os.path.expanduser(imgURL)
        if os.path.exists(imgURL):
            # Probably a file, read it in binary and extract the data
            # then return.
            content = open(imgURL, "rb").read()
            with open(COVER_IMG, 'wb') as f:
                f.write(content)
            return True

        # Else might be an URL
        try:
            # Try to get 512 cover art
            imgURL = imgURL.replace('100x100', '2048x2048')
        except Exception:
            pass

        r = requests.get(imgURL)

        with open(COVER_IMG, 'wb') as f:
            f.write(r.content)

        return True
    except TimeoutError:
        prepend(2)
        print('Could not get album cover. Are you connected to internet?\a')
        return False
    except Exception as e:
        logger.warning(
            "Error while trying to download image, skipping!: {}".format(e))
        return False


def set_MP3_data(song, song_path):
    """
    Set the meta data if the passed data is mp3.
    """
    # A variable to see if cover image was added.
    IS_IMG_ADDED = False

    try:
        audio = MP3(song_path, ID3=ID3)
        data = ID3(song_path)

        # Download the cover image, if failed, pass
        if dwCover(song):
            imagedata = open(COVER_IMG, 'rb').read()
            data.add(APIC(3, 'image/jpeg', 3, 'Front cover', imagedata))
            # Remove the image
            os.remove(COVER_IMG)
            IS_IMG_ADDED = True

        # If tags are not present then add them
        try:
            audio.add_tags()
        except Exception:
            pass

        audio.save()

        logger.debug("Passed song release date: ", song.release_date)

        data.add(TYER(encoding=3, text=song.release_date))
        data.add(TIT2(encoding=3, text=song.track_name))
        data.add(TPE1(encoding=3, text=song.artist_name))
        data.add(TALB(encoding=3, text=song.collection_name))
        data.add(TCON(encoding=3, text=song.primary_genre_name))
        data.add(TRCK(encoding=3, text=str(song.track_number)))

        data.save()
        return IS_IMG_ADDED

    except Exception as e:
        logger.debug("{}".format(e))
        return e, False


def prepend(state):
    """PREPEND is used to print ==> in front of the lines.
    They are colorised according to their status.
    If everything is good then green else red.
    """
    # State 1 is for ok
    # State 2 is for notok

    print(Style.BRIGHT, end='')
    if state == 1:
        print(Fore.LIGHTGREEN_EX, end='')
    elif state == 2:
        print(Fore.LIGHTRED_EX, end='')
    else:
        pass

    print(' ==> ', end='')
    print(Style.RESET_ALL, end='')


def setData(SONG_INFO, song_path):
    """Add the metadata to the song."""
    song = SONG_INFO[0]

    get_more_data_dict = {
        'deezer': deezer.get_more_data,
        'lastfm': lastfm.get_more_data,
        'musicbrainz': musicbrainz.get_more_data
    }

    # Try to check if the song object has an attribute provider
    # Deezer has it but other objects don't have it.
    # If the provider is present then fetch extra data accordingly

    if hasattr(song, 'provider') and song.provider in get_more_data_dict:
        song = get_more_data_dict.get(song.provider, lambda _: None)(song)

    img_added = set_MP3_data(
        song,
        song_path,
    )

    # Show the written stuff in a better format
    prepend(1)
    print('================================')
    print('  || YEAR: ' + song.release_date)
    print('  || TITLE: ' + song.track_name)
    print('  || ARTIST: ' + song.artist_name)
    print('  || ALBUM: ' + song.collection_name)
    print('  || GENRE: ' + song.primary_genre_name)
    print('  || TRACK NO: ' + str(song.track_number))

    if img_added:
        print('  || ALBUM COVER ADDED')

    prepend(1)
    print('================================')


def get_from_itunes(SONG_NAME):
    """Try to download the metadata using itunespy."""
    # Try to get the song data from itunes
    return itunespy.search_track(SONG_NAME, country='US')


def get_from_gaana(SONG_NAME):
    """Get some tags from gaana."""
    return gaana.searchSong(SONG_NAME)


def get_from_deezer(SONG_NAME):
    """Get some tags from deezer."""
    return deezer.searchSong(SONG_NAME)


def get_from_lastfm(SONG_NAME):
    """Get metadata from Last FM"""
    return lastfm.searchSong(SONG_NAME)


def get_from_saavn(SONG_NAME):
    """
    Get the songs from JioSaavn
    """
    return saavn.search_query(SONG_NAME)


def get_from_musicbrainz(SONG_NAME):
    """Get the songs from musicbrainz"""
    return musicbrainz.search_song(SONG_NAME)


def get_from_spotify(SONG_NAME):
    """
    Get the songs from Spotify
    """
    return spotify.search_song(SONG_NAME, country='US')


def _extend_to_be_sorted_and_rest(provider_data, to_be_sorted, rest):
    """Create the to be sorted and rest lists"""
    # Before passing for sorting filter the songs
    # with the passed args
    if provider_data is not None:
        to_be_sorted.extend(provider_data[:10])
        rest.extend(provider_data[10:])


def search_song(q="Tera Buzz"):
    """Do the task by calling other functions."""
    to_be_sorted = []
    rest = []

    metadata_providers = ["itunes", "spotify", "gaana"]

    GET_METADATA_ACTIONS = {
        'itunes': get_from_itunes,
        'gaana': get_from_gaana,
        'deezer': get_from_deezer,
        'saavn': get_from_saavn,
        'lastfm': get_from_lastfm,
        'musicbrainz': get_from_musicbrainz,
        'spotify': get_from_spotify
    }

    for provider in metadata_providers:
        if provider in GET_METADATA_ACTIONS:
            logger.debug(f"Searching metadata with {provider}")
            try:
                data_provider = GET_METADATA_ACTIONS.get(
                    provider, lambda _: None)(q)
                if data_provider:
                    for i in data_provider:
                        setattr(i, 'provider', provider)
                    _extend_to_be_sorted_and_rest(
                        data_provider, to_be_sorted, rest)
            except Exception:
                pass
        else:
            logger.warning(
                '"{}" isn\'t implemented. Skipping!'.format(provider)
            )

    if not to_be_sorted:
        return None

    # Add the unsorted data
    to_be_sorted += rest

    return to_be_sorted


def meta(conv_name: str, song_name: str, song_metadata: str):
    """Handle adding the metadata for the passed song.

    We will use the passed name to search for metadata, ask
    the user for a choice and accordingly add the meta to
    the song.
    """
    # Else add metadata in ordinary way
    logger.info('Getting song data for {}...'.format(song_name))
    track_info = search_song(song_name)
    if track_info is None:
        logger.info('Getting song data for {}...'.format(song_metadata))
        track_info = search_song(song_metadata)
    if track_info is None:
        logger.info(f"can not found metadata for song: {song_name}")
        return
    logger.info('Setting data...')
    setData(
        track_info,
        conv_name,
    )


def clean_dir(prepare_deleted_p):
    for _s in glob.glob(prepare_deleted_p):
        os.remove(_s)
        logger.debug('Removed "{}" from cache'.format(_s))


def post_processing(
        song_name: str,
        song_metadata: str,
        path: str,
) -> None:
    """Handle all the activities post search of the song.

    This function will handle the following:
    Convert, Trim, Metadata, Cleaning up.
    """
    logger.debug("song_name: ", song_name, " song_meta: ", song_metadata)
    conv_name = convert_to_mp3(path)

    extension = os.path.basename(conv_name).split(".")[-1]
    logger.debug("ext: {}".format(extension))

    new_basename = "{}.{}".format(song_name, extension)

    logger.debug("Moving to: {}".format(dest_dir))

    # Create the destination file name
    dest_filename = os.path.join(
        dest_dir, re.sub(r'/', '-', new_basename))
    meta(conv_name, song_name, song_metadata)
    shutil.move(conv_name, dest_filename)

    logger.info('Moved to {}...'.format(dest_dir))
    # Delete the cached songs
    clean_dir(
        os.path.join(
            TMP_PATH,
            'music',
            '*'
        )
    )
    logger.info("Done")


if __name__ == '__main__':
    songs, playlist_name = get_playlist(play_list)
    for song in songs:
        try:
            # 清理目录
            clean_dir(
                os.path.join(
                    TMP_PATH,
                    '*.jpg'
                )
            )
            clean_dir(
                os.path.join(
                    TMP_PATH,
                    "music",
                    '*'
                )
            )
            url = song["url"]
            song_name = get_title(url)
            link, yt_title = search(url=url)
            # Check if this song is supposed to be skipped.
            if not link:
                logger.warning("Skipping this song!")
                continue
            path = download(link, yt_title)
            # Try to extract the chapters
            ydl_opts_cp = copy.deepcopy(ydl_opts)
            # info = yt_dlp.YoutubeDL(ydl_opts_cp).extract_info(url, False)

            post_processing(
                yt_title,
                song_name,
                path,
            )
        except Exception:
            logger.info(f"failed to downloading {song_name}", exc_info=True)
