#!/usr/bin/python3

import glob
import json
import datetime
import os
import re
import sys
from subprocess import call, CalledProcessError, check_output, DEVNULL

# get all mkv files in current directory, sort episodes alphabetically
mkv_filenames = glob.glob("*.mkv")
mkv_filenames.sort()

# create and populate dictionary mapping op/ed segment UIDs to filenames
songs = {}
for filename in mkv_filenames:
    if ("OP" in filename) or ("ED" in filename):
        # write mkvinfo to a file, then grep for the segment UID
        with open("mkv_info.txt", "w") as mkvinfo:
            call(["mkvinfo", filename], stdout=mkvinfo, stderr=DEVNULL)
        segment_uid = check_output('grep "Segment UID" mkv_info.txt', shell=True).decode("utf-8").split(":")[-1].strip()
        
        # this way we can easily figure out which file the ordered chapter is linking in later
        songs[segment_uid] = filename

# loop over all mkv files in the current directory
for mkv_file in mkv_filenames:
    # ignore any files with "OP" or "ED" in the filename, since we already dealt with those
    if ("OP" in mkv_file) or ("ED" in mkv_file):
        continue

    ordered_chapters = {}   # dictionary mapping chapter UIDs to segment UIDs if chapter is ordered
    chapter_timecodes = []  # array of timecode dicts containing "start" and "end" as keys

    # once again, write mkvinfo to a file, but this time grep for a bunch of chapter info
    with open("mkv_info.txt", "w") as mkvinfo:
        call(["mkvinfo", mkv_file], stdout=mkvinfo, stderr=DEVNULL)
    with open("mkv_info.txt", "r") as mkvinfo:
        chapter_info = mkvinfo.read().split("|+ Chapters")[-1]
        chapters = chapter_info.split("|  + Chapter atom")

        # loop through chapters, ignoring the edition info before the first instance of "Chapter atom"
        for y in range(len(chapters)):
            if y == 0:
                continue

            # write just this portion to a file so it can be grepped without interference
            with open("chapter_info.txt", "w") as info:
                info.write(chapters[y])
            with open("chapter_info.txt", "r") as info:
                chapter_uid = re.search(r'\d+', check_output('grep "Chapter UID" chapter_info.txt', shell=True).decode("utf-8")).group()
                try:
                    segment_uid = check_output('grep "segment UID" chapter_info.txt', shell=True).decode("utf-8").split(":")[-1].strip()
                    ordered_chapters[chapter_uid] = segment_uid
                except CalledProcessError:
                    # if there's no segment UID, then this chapter isn't referring to an external file
                    pass
                start_time = check_output('grep "Chapter time start" chapter_info.txt', shell=True).decode("utf-8").split(": ")[-1][3:-6]
                end_time = check_output('grep "Chapter time end" chapter_info.txt', shell=True).decode("utf-8").split(": ")[-1][3:-6]
                timecodes = { "chapter_uid": chapter_uid, "start": start_time, "end": end_time }
                chapter_timecodes.append(timecodes)

        op_filename = ""
        ed_filename = ""
        op_timecode = ""
        ed_timecode = ""
        op_first = False
        ed_last = False

        # loop through chapters to find insert points
        # NOTE: this will set the ED as the OP if there is no OP this episode (keep this in mind for mkvmerge)
        for y in range(len(chapter_timecodes)):
            try:
                segment_uid = ordered_chapters[chapter_timecodes[y]["chapter_uid"]]
                if y == len(chapter_timecodes) - 1:
                    ed_last = True
                if len(op_filename) == 0:
                    op_filename = songs[segment_uid]
                else:
                    ed_filename = songs[segment_uid]
                # if this is not the first chapter, then the insert point is the end of the previous chapter
                if (y > 0):
                    if len(ed_filename) == 0:
                        op_timecode = chapter_timecodes[y - 1]["end"]
                    else:
                        ed_timecode = chapter_timecodes[y - 1]["end"]
                else:
                    # if this IS the first chapter, then the OP insert point is just 00:00
                    op_first = True
                    op_timecode = chapter_timecodes[y]["start"]
            except KeyError:
                # only here to prevent Python from throwing a fit when the key doesn't match
                pass

        # hard code styles for use later in sorting ass file
        call(["ffmpeg", "-i", mkv_file, "main.ass", "-y"])
        style = ""
        with open("main.ass", "r") as main_ass:
            style = "\n".join(main_ass.read().split("[V4+ Styles]\n")[-1].split("\n\n[Events]")[0].split("\n")[1:])

        # split file by op/ed insert points (yes, this magically still works even if they're right at the edge of the file)
        with open("concat_list.txt", "w") as concat_list:
            call(["mkvmerge", "-o", "temp.mkv", "--split", "timecodes:" + op_timecode + "," + ed_timecode, "--no-chapters", mkv_file])

            # if the episode contains both OP and ED
            if len(ed_timecode) > 0:
                # if there's a prologue and an epilogue, put them between those and the main content
                if not (op_first or ed_last):
                    call(["mkvmerge", "-o", mkv_file, "temp-001.mkv", "+" + op_filename, "+temp-002.mkv", "+" + ed_filename, "+temp-003.mkv"])
                    concat_list.write("file 'temp-001.mkv'\n")
                    concat_list.write("file '" + op_filename + "'\n")
                    concat_list.write("file 'temp-002.mkv'\n")
                    concat_list.write("file '" + ed_filename + "'\n")
                    concat_list.write("file 'temp-003.mkv'\n")

                # if there's neither a prologue nor an epilogue, put the main episode between OP and ED
                elif (op_first and ed_last):
                    call(["mkvmerge", "-o", mkv_file, op_filename, "+temp-001.mkv", "+" + ed_filename])
                    concat_list.write("file '" + op_filename + "'\n")
                    concat_list.write("file 'temp-001.mkv'\n")
                    concat_list.write("file '" + ed_filename + "'\n")

                # if there's an epilogue but not a prologue...
                elif op_first:
                    call(["mkvmerge", "-o", mkv_file, op_filename, "+temp-001.mkv", "+" + ed_filename, "+temp-002.mkv"])
                    concat_list.write("file '" + op_filename + "'\n")
                    concat_list.write("file 'temp-001.mkv'\n")
                    concat_list.write("file '" + ed_filename + "'\n")
                    concat_list.write("file 'temp-002.mkv'\n")

                # if there's a prologue but not an epilogue...
                else:
                    call(["mkvmerge", "-o", mkv_file, "temp-001.mkv", "+" + op_filename, "+temp-002.mkv", "+" + ed_filename])
                    concat_list.write("file 'temp-001.mkv'\n")
                    concat_list.write("file '" + op_filename + "'\n")
                    concat_list.write("file 'temp-002.mkv'\n")
                    concat_list.write("file '" + ed_filename + "'\n")

            # if the episode contains one or the other song, but not both
            elif len(op_timecode) > 0:
                # if there's a prologue and main episode (or a main episode and epilogue if this is the ED)
                if not (op_first or ed_last):
                    call(["mkvmerge", "-o", mkv_file, "temp-001.mkv", "+" + op_filename, "+temp-002.mkv"])
                    concat_list.write("file 'temp-001.mkv'\n")
                    concat_list.write("file '" + op_filename + "'\n")
                    concat_list.write("file 'temp-002.mkv'\n")

                # if there's a main episode and the song is last
                elif ed_last:
                    call(["mkvmerge", "-o", mkv_file, "+temp-001.mkv", "+" + op_filename])
                    concat_list.write("file 'temp-001.mkv'\n")
                    concat_list.write("file '" + op_filename + "'\n")

                # if there's a main episode and the song is first
                # (technically the case where the episode is just the song would hit this block, but that should never happen)
                else:
                    call(["mkvmerge", "-o", mkv_file, "+" + op_filename, "+temp-001.mkv"])
                    concat_list.write("file '" + op_filename + "'\n")
                    concat_list.write("file 'temp-001.mkv'\n")

            # if both are missing, obviously we don't want to do anything with the original file (the mkvmerge command should error out in this case)
            else:
                # remove temp files (not absolutely necessary as they should be overwritten anyway, but just to make sure)
                temp_files = glob.glob("temp-*.mkv")
                for temp_file in temp_files:
                    call(["rm", temp_file])
                call(["rm", "chapter_info.txt", "main.ass"])

                # skip the rest of this iteration because if there's op/ed to process, then the original file should have no playback issues
                continue

        # run ffmpeg command to merge videos
        ffmpeg_array = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", "concat_list.txt", "-c:v", "copy", "-map", "0:v", "-c:a", "copy", "-map", "0:a", "-c:s", "copy", "-map", "0:s"]

        ffmpeg_array.append("temp-000.mkv")
        ffmpeg_array.append("-y")

        call(ffmpeg_array)

        # extracting subtitles so we can sort by timecodes and add the main style back in
        call(["ffmpeg", "-i", "temp-000.mkv", "temp.ass", "-y"])

        ass_start = ""
        ass_end = []
        with open("temp.ass", "r") as ass_file:
            # we don't need to sort anything before the Dialogue lines
            ass_array = ass_file.read().split("Dialogue:")
            ass_start = ass_array[0]

            # add in the style from the main episode
            ass_start = ass_start.replace("\n\n[Events]", "\n" + style + "\n\n[Events]")

            # sort the Dialogue lines
            ass_end = ass_array[1:]
            ass_end.sort()

        # write the sorted subtitles back to a new ass file
        with open("sorted.ass", "w") as ass_sort:
            ass_sort.write(ass_start)
            for dialogue in ass_end:
                ass_sort.write("Dialogue:" + dialogue)

        # need to rename file produced by mkvmerge so that the output file can have that name
        # (ffmpeg will overwrite the input file and crash if the input and output filenames are the same)
        call(["mv", mkv_file, "temp-004.mkv"])

        """
        What we're putting into ffmpeg:
            
            1. The combined (prologue)? OP main ED (epilogue)? file produced by mkvmerge
            2. The subtitle file, sorted by timecodes with styles from all parts
            3. The combined (prologue)? OP main ED (epilogue)? file produced by ffmpeg

        What this command outputs:

            1. The video stream from #3, because ffmpeg is better at concatenating video (mkvmerge produces artifacts)
            2. The audio stream from #1, because it works
            3. The subtitle stream from #2, because we needed a separate file to sort them and add the main styles back in
            4. The attachment stream from #1, because mkvmerge is better at concatenating attachments (for lack of better phrasing)
        """
        ffmpeg_array = ["ffmpeg", "-i", "temp-004.mkv", "-i", "sorted.ass", "-i", "temp-000.mkv",
                        "-c:v", "copy", "-map", "2:0", "-c:a", "copy", "-map", "0:1", "-map", "1:0", "-map", "0:t",
                        "-disposition:s:s:0", "default", "-metadata:s:s:0", "language=eng", mkv_file, "-y"]
        call(ffmpeg_array)

        # remove temp files (not absolutely necessary as they should be overwritten anyway, but just to make sure)
        temp_files = glob.glob("temp-*.mkv")
        for temp_file in temp_files:
            call(["rm", temp_file])
        call(["rm", "chapter_info.txt", "main.ass", "concat_list.txt"])

call(["rm", "mkv_info.txt"])

# remove the now unnecessary op/ed files
for song in songs:
    call(["rm", songs[song]])
