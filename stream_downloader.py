import re
import os
import time
import m3u8
import requests
import argparse
import subprocess
from Crypto.Cipher import AES
from fake_useragent import UserAgent


def getRequest(url, sc, cookies, headers):
    response = requests.get(url, cookies=cookies, headers=headers)
    if response.status_code != sc:
        print(f"Invalid status code {response.status_code} vs required: {sc}")
        print(f"  URL:     {url}")
        print(f"  cookies: {cookies}")
        print(f"  Headers: {headers}")
        exit(1)
    return response

def errExit(msg, rc):
    print(msg)
    exit(rc)


BASE_URL = "https://is.muni.cz/"

ap = argparse.ArgumentParser()
ap.add_argument("-u", "--url",    required=True, help="IS webpage containing stream (VOD)")
ap.add_argument("--issession",    required=True, help="session cookie")
ap.add_argument("--iscreds",      required=True, help="credential cookie")
ap.add_argument("-f", "--ffmpeg", required=True, help="Path to ffmpeg executable")
args = vars(ap.parse_args())

cookies     = {"issession": args["issession"], "iscreds" : args["iscreds"]}
referer     = args["url"]
ffmpeg_path = args["ffmpeg"]


#get possible file name
response = getRequest(referer, 200, cookies, None)
page = response.text
course        = referer.split("/")[-2]
lecture_title = re.findall(r'<h1\sclass="io-verejne">(.+)<\/h1>', page)[0]

#get encode key
page = page.replace(" ", "")
encode_key = re.findall(r"\"encode_key\":\".+\"", page)
if (len(encode_key) < 1):
    errExit("No encode key found. Exiting...", 1)
encode_key = encode_key[0].replace('"', "").split(":")[1]

#get master uri and data path
master_uri = re.findall(r"src=\".+\.m3u8\"", page, re.MULTILINE)
if (len(master_uri) < 1):
    errExit("No master file found. Exiting...", 1)
master_uri = master_uri[0].replace("src=", "").replace('"', "")[1:]
stream_data_path = master_uri[:-11]


#get streams from master
response = getRequest(f"{BASE_URL}{master_uri}", 200, cookies, None)
playlists = m3u8.loads(response.text).data["playlists"]

stream_cnt = len(playlists)
if stream_cnt < 1:
    errExit("No streams found. Exiting...", 1)

#print and select streams
print(f"Found {stream_cnt} stream(s):")
i = 0
for playlist in playlists:
    i += 1
    print(f"  {i}: {playlist['uri']}")
    print(f"    Resolution: {playlist['stream_info']['resolution']}")
    print(f"    Bandwidth:  {playlist['stream_info']['bandwidth']}")
    print(f"    Codec:      {playlist['stream_info']['codecs']}")

selected = 1
if stream_cnt > 1:
    selected = input(f"Please select stream (1 - {i}): ")
    while not selected.isdigit() or int(selected) < 1 or int(selected) > i:
        selected = input("Invalid option. Try again: ")


#get selected stream
stream_uri      = playlists[int(selected) - 1]["uri"]
selected_stream = stream_uri.split("/")[0]
response        = getRequest(f"{BASE_URL}{stream_data_path}{stream_uri}", 200, cookies, None)
m3u8_file       = m3u8.loads(response.text)

#extract key uri from stream
key = m3u8_file.data["keys"]
if len(key) != 1:
    print(f"Found invalid number of keys: {len(key)}. Only 1 allowed")
    exit(1)
key_uri = key[0]["uri"][1:]

#get and calculate decription key
response       = getRequest(f"{BASE_URL}{key_uri}", 200, cookies, None)
decription_key = list(response.content)
encode_key     = re.findall(r"..", encode_key)
encode_key     = [int(byte, 16) for byte in encode_key]
for i in range(0, len(decription_key)):
    decription_key[i] = decription_key[i] ^ encode_key[i]

#extract segments
segments = m3u8_file.data["segments"]
print(f"Found {len(segments)} segments")

#download individual parts, decode them and write them intu a single file
i = 0
with open(f"video.ts", "wb") as f:
    decryptor = AES.new(bytes(decription_key), AES.MODE_CBC)
    ua        = UserAgent()
    for segment in segments:
        i += 1
        start        = int(segment["byterange"].split("@")[1])
        length       = int(segment["byterange"].split("@")[0])
        ts_uri       = segment["uri"]
        request_time = int(time.time())
        headers = {
            "user-agent": ua.chrome,
            "range": f"bytes={start}-{start + length - 1}",
            "referer": referer
        }
        response = getRequest(f"{BASE_URL}{stream_data_path}{selected_stream}/{ts_uri}?t={request_time}", 206, cookies, headers)
        dec_data = decryptor.decrypt(response.content)
        f.write(dec_data)
        print(f"Downloading segment {i}/{len(segments)} ({start} {length}): {selected_stream}/{ts_uri}?t={request_time}")


#convert the ts to mp4 using ffmpeg
subprocess.run([ffmpeg_path, '-i', 'video.ts', '-c', 'copy', f'{course} - {lecture_title}.mp4'])

if input("Do you want to remove the '.ts' file? (Y/n): ") != "n":
    os.remove("video.ts")

print("Done...")
