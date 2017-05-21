#/usr/bin/python3

###################################################################
#
# gst2.py - main pycorder script
#
# Description:
#   - manages GStreamer pipelines
#   - initializes OSD 
#
###################################################################

from gi.repository import GObject
from gi.repository import Gst
import glob
import paho.mqtt.client
import subprocess
import time
import mqtt_gst_osd
from mqtt_gst_osd import MenuItem as mi
from collections import OrderedDict
import datetime
import os

Gst.debug_set_active(True)
Gst.debug_set_default_threshold(3)

RECORDING_ROOT="/var/recordings/"

def make_play_callback(name):
    def play_file(menu):
        preview_pipeline.set_state(Gst.State.NULL)
        
        # omx decoder does not seem to be able to handle cycle NULL -> PLAYING -> NULL -> PLAYING (freezes)
        # so it has to be replaced before each playback... (facepalm)
        decode = play_pipeline.get_by_name("play_decoder")
        play_pipeline.remove(decode)
        decode = Gst.ElementFactory.make("omxh264dec", "play_decoder")
        play_pipeline.add(decode)
        play_parser.link(decode)
        decode.link(play_queue)
        play_src.set_property( "location", name )
        
        play_pipeline.set_state(Gst.State.PLAYING)
        
    return play_file

def recordings_menu(menu):
    
    return [
        mi(os.path.basename(x), make_play_callback(x)) for x in sorted(glob.glob("/var/recordings/*.avi"))
    ]

def start_rec(menu):
    preview_pipeline.set_state( Gst.State.NULL )
    rec_sink.set_property("location", datetime.datetime.now().strftime(RECORDING_ROOT+"/rec_%Y%m%d_%H%M.avi"))
    menu.minimize()
    rec_pipeline.set_state(Gst.State.PLAYING)

def stop_rec(menu):
    rec_pipeline.set_state( Gst.State.NULL )
    menu.restore()
    preview_pipeline.set_state(Gst.State.PLAYING)

def shutdown(menu):
    os.system("halt")

def reboot(menu):
    os.system("reboot")

def toggle_menu(menu):
    if menu.visible():
        menu.minimize()
    else:
        menu.restore()

def back_to_menu(menu):
    if preview_pipeline.get_state(0)[1] == Gst.State.PLAYING:
        return
    if rec_pipeline.get_state(0)[1] != Gst.State.NULL:
        return stop_rec(menu)
    if play_pipeline.get_state(0)[1] != Gst.State.NULL:
        play_pipeline.set_state(Gst.State.NULL)
        #play_pipeline.remove(play_decode)
        menu.restore()
        preview_pipeline.set_state(Gst.State.PLAYING)

def on_message( bus, message):
    t = message.type
    if t == Gst.MessageType.EOS:
    	back_to_menu(osd) # !! refactor: references global variable
    elif t == Gst.MessageType.ERROR:
    	print( message.parse_error() )


# text, action, enabled=True, submenu=None
menu = [
    mi("Start rec", start_rec),
    mi("Recordings", submenu=recordings_menu),
    mi("Shutdown", submenu=[
        mi("Really SHUTDOWN?", shutdown)
    ]),
    mi("Reboot", submenu=[
        mi("Really REBOOT?", reboot)
    ]),
]

GObject.threads_init()
Gst.init(None)

#### PREVIEW PIPELINE
preview_pipeline = Gst.Pipeline()
source = Gst.ElementFactory.make("v4l2src", "source")
source.set_property("device", "/dev/video0")
source.set_property("norm", "PAL")
preview_pipeline.add(source)

incaps = Gst.caps_from_string("video/x-raw,format=RGB")
infilter = Gst.ElementFactory.make("capsfilter", "infilter")
infilter.set_property("caps", incaps)
preview_pipeline.add(infilter)
source.link(infilter)

### screen output
screen_queue = Gst.ElementFactory.make("queue", "screen_queue")
preview_pipeline.add(screen_queue)
infilter.link(screen_queue)

overlay = Gst.ElementFactory.make("textoverlay", "overlay")
overlay.set_property( "text", "" )
overlay.set_property( "font-desc", "Monospace 36" )
overlay.set_property( "valignment", "top" )
overlay.set_property( "halignment", "left" )
overlay.set_property( "line-alignment", "left" )
preview_pipeline.add(overlay)
screen_queue.link(overlay)

convert = Gst.ElementFactory.make("videoconvert", "convert")
preview_pipeline.add(convert)
overlay.link(convert)

#screen_fb_queue = Gst.ElementFactory.make("queue", "screen_fb_queue") # no effect with overlay :-(
#preview_pipeline.add(screen_fb_queue)
#convert.link(screen_fb_queue)

fbsink = Gst.ElementFactory.make("fbdevsink", "fbsink")
preview_pipeline.add(fbsink)
#screen_fb_queue.link(fbsink)
convert.link(fbsink)


### RECORDING PIPELINE
rec_pipeline = Gst.Pipeline()

rec_source = Gst.ElementFactory.make("v4l2src", "source")
rec_source.set_property("device", "/dev/video0")
rec_source.set_property("norm", "PAL")
rec_pipeline.add(rec_source)

rec_queue1 = Gst.ElementFactory.make("queue", "rec_queue1")
rec_pipeline.add(rec_queue1)
rec_source.link(rec_queue1)

rec_encoder = Gst.ElementFactory.make("omxh264enc", "rec_encoder")
rec_encoder.set_property("target-bitrate", 4000000)
rec_encoder.set_property("control-rate", "variable")
rec_pipeline.add(rec_encoder)
rec_queue1.link(rec_encoder)

rec_caps = Gst.caps_from_string("video/x-h264,profile=high")
rec_filter = Gst.ElementFactory.make("capsfilter", "rec_filter2")
rec_filter.set_property("caps", rec_caps)
rec_pipeline.add(rec_filter)
rec_encoder.link(rec_filter)

rec_queue2 = Gst.ElementFactory.make("queue", "rec_queue2")
rec_pipeline.add(rec_queue2)
rec_filter.link(rec_queue2)

rec_parser = Gst.ElementFactory.make("h264parse", "rec_parser")
rec_pipeline.add(rec_parser)
rec_queue2.link(rec_parser)

rec_muxer = Gst.ElementFactory.make("avimux", "rec_muxer")
rec_pipeline.add(rec_muxer)
rec_parser.link(rec_muxer)

# seems to reduce some of the artifacts in the recording
rec_queue3 = Gst.ElementFactory.make("queue", "rec_queue3")
rec_pipeline.add(rec_queue3)
rec_muxer.link(rec_queue3)

rec_sink = Gst.ElementFactory.make("filesink", "rec_sink")
rec_pipeline.add(rec_sink)
rec_sink.set_property("location", "out.avi")
rec_queue3.link(rec_sink)

# rec_pipeline video out
rec_src = Gst.ElementFactory.make("videotestsrc", "rec_src")
rec_src.set_property("pattern", "black")
rec_pipeline.add(rec_src)

# 5 FPS is enough for time display, higher framerate affects recording...
rec_screen_caps = Gst.caps_from_string("video/x-raw,width=720,height=576,framerate=5/1")
rec_screen_filter = Gst.ElementFactory.make("capsfilter", "rec_screen_filter")
rec_screen_filter.set_property("caps", rec_screen_caps)
rec_pipeline.add(rec_screen_filter)
rec_src.link(rec_screen_filter)

rec_overlay = Gst.ElementFactory.make("textoverlay", "overlay")
rec_overlay.set_property( "text", "\n\nRecording (00:00)" )
rec_overlay.set_property( "font-desc", "Monospace 36" )
rec_overlay.set_property( "valignment", "top" )
rec_overlay.set_property( "halignment", "left" )
rec_overlay.set_property( "line-alignment", "left" )
rec_pipeline.add(rec_overlay)
rec_screen_filter.link(rec_overlay)

rec_disp_convert = Gst.ElementFactory.make("videoconvert", "rec_disp_convert")
rec_pipeline.add(rec_disp_convert)
rec_overlay.link(rec_disp_convert)

rec_fbsink = Gst.ElementFactory.make("fbdevsink", "rec_fbsink")
rec_fbsink.set_property( "sync", "false" )
rec_pipeline.add(rec_fbsink)
rec_disp_convert.link(rec_fbsink) 


## Playback pipeline
# gst-launch-1.0 filesrc location=out.avi ! avidemux ! h264parse ! omxh264dec ! fbdevsink
def play_connect_demux( demux, pad ):
    try:
        pad.link( play_parser.get_static_pad("sink") )
    except:
        raise


play_pipeline = Gst.Pipeline()
play_bus = play_pipeline.get_bus()
play_bus.add_signal_watch()
play_bus.connect("message", on_message)

play_src = Gst.ElementFactory.make("filesrc", "play_src")
play_pipeline.add(play_src)

play_demux = Gst.ElementFactory.make("avidemux", "play_demux")
play_pipeline.add(play_demux)
play_src.link(play_demux)
play_demux.connect("pad-added", play_connect_demux) # pad created dynamically

play_parser = Gst.ElementFactory.make("h264parse", "play_parser")
play_pipeline.add(play_parser)

play_decode = Gst.ElementFactory.make("omxh264dec", "play_decoder")
play_pipeline.add(play_decode)
play_parser.link(play_decode)

play_queue = Gst.ElementFactory.make("queue", "play_queue") # prevents frame drops (frame too late)
play_pipeline.add(play_queue)
play_decode.link(play_queue)

play_overlay = Gst.ElementFactory.make("textoverlay", "overlay")
play_overlay.set_property( "text", "Playing (00:00)" )
play_overlay.set_property( "font-desc", "Monospace 22" )
play_overlay.set_property( "valignment", "top" )
play_overlay.set_property( "halignment", "left" )
play_overlay.set_property( "line-alignment", "left" )
play_pipeline.add(play_overlay)
play_queue.link(play_overlay)

play_sink = Gst.ElementFactory.make("fbdevsink", "play_sink")
play_pipeline.add(play_sink)
play_overlay.link(play_sink)


##

preview_pipeline.set_state(Gst.State.PLAYING)

osd = mqtt_gst_osd.MqttGstOsd( overlay, 5, menu )
osd.on_button(2, toggle_menu)
osd.on_button(1, back_to_menu)
osd.run(True) # run OSD in separate thread

def pipeline_time(pipe):
    pos = pipe.query_position(Gst.Format.TIME)
    dt = datetime.datetime.fromtimestamp(round(pos[1]/1000000000))
    return dt.strftime("%M:%S")


try:
    while True:
		# update positions of pipelines in corresponding OSDs
		rec_overlay.set_property( "text", "\n\nRecording (%s)" % pipeline_time(rec_pipeline) )
        play_overlay.set_property( "text", "Playing (%s)" % pipeline_time(play_pipeline) )

        time.sleep(1)
finally:
    preview_pipeline.set_state( Gst.State.NULL )

