#!/usr/bin/python3

######################################################################
#
#  joystick3,py - an MQTT joystick device
#
#  Reads /dev/input/joy0 and relays button/axes states to MQTT topic:
#    buttons: /device/joystick/1/<button_number>
#    axes:    /device/joystick/2/<axis_number>
#
######################################################################

import struct
import time
import queue
import threading
import collections
import paho.mqtt.client
import sys

Event = collections.namedtuple("Event", ["time", "value", "type", "number"])

events = queue.Queue()

def entry_point():
  file = None
  while True:
    try:
      if file == None:
        file = open("/dev/input/js0", "rb")
      buff = file.read(8)
      input = Event._make(struct.unpack('IhBB', buff))
      
      if input.type & 0x80:
        continue # filter device init state mssages
      
      events.put(input)
    except:
      file = None
      time.sleep(1.0)

thread = threading.Thread(target=entry_point)
thread.daemon = True
thread.start()

mqtt = paho.mqtt.client.Client()
mqtt.connect("127.0.0.1", 1883, 60)

while True:
  while not events.empty():
    event = events.get()
    mqtt.publish("/device/joystick/" + str(event.type) + "/" + str(event.number), event.value)
  mqtt.loop(0.2, 10)
