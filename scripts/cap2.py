#! /usr/bin/env python3

import argparse
import configparser
from datetime import datetime, timezone
import os
import psycopg2
import pyshark
import sys
import traceback
import ipaddress
import peewee
import logging

# constants
DEBUG_LEVEL = logging.DEBUG
ARETHA_BASE = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]
CONFIG_PATH = os.path.sep.join((ARETHA_BASE, "config", "config.cfg"))
LOG_PATH = os.path.sep.join((ARETHA_BASE, 'log'))
MODULENAME = 'cap2'
logFormatter = logging.Formatter("%(asctime)s {}::%(levelname)s %(message)s".format(MODULENAME))
log = logging.getLogger()
log.setLevel(DEBUG_LEVEL)
fileHandler = logging.FileHandler(os.path.sep.join((LOG_PATH, '%s.log' % MODULENAME)))
fileHandler.setFormatter(logFormatter)
log.addHandler(fileHandler)
consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
log.addHandler(consoleHandler)

# initialise vars
timestamp = 0
queue = []
COMMIT_INTERVAL = None
CONFIG = None

def fix_sniff_tz(sniff_dt):
    # pyshark doesn't return an aware datetime, so we misinterpret things to being non local
    # what we need to do is figure out what timezone we captured in
    local_tz = datetime.utcnow().astimezone().tzinfo
    # then we need to create a new datetime with the actual timezone... cast back into UTC.
    return datetime.combine(sniff_dt.date(),sniff_dt.time(),local_tz).astimezone(timezone.utc)

def DatabaseInsert(packets):
    global timestamp

    # open db connection
    database = CONFIG['postgresql']['database']
    username = CONFIG['postgresql']['username']
    password = CONFIG['postgresql']['password']
    conn = psycopg2.connect(f"dbname={database} user={username} password={password}")
    cur = conn.cursor()

    insert = "INSERT INTO packets (time, src, dst, mac, len, proto, ext) VALUES "
    for packet in packets:
        # clean up packet info before entry
        mac = ''
        src = ''
        dst = ''
        proto = ''
        ext = ''

        if 'ip' not in packet:
            continue

        try:
            src = packet.ip.src
            dst = packet.ip.dst
        except AttributeError as ke:
            log.error("AttributeError determining src/dst", exc_info=ke)
            continue

        if ipaddress.ip_address(src).is_multicast or ipaddress.ip_address(dst).is_multicast or packet['eth'].src == 'ff:ff:ff:ff:ff:ff' or  packet['eth'].dst == 'ff:ff:ff:ff:ff:ff':
            continue

        srcLocal = ipaddress.ip_address(src).is_private
        dstLocal = ipaddress.ip_address(dst).is_private

        if srcLocal == dstLocal:
            continue  # internal packet that we don't care about, or no local host (should never happen)
        elif not dstLocal:
            mac = packet['eth'].src
            ext = packet.ip.dst
        else:
            mac = packet['eth'].dst
            ext = packet.ip.src

        if len(packet.highest_layer) > 10:
            proto = packet.highest_layer[:10]
        else:
            proto = packet.highest_layer

        # insert packets into table
        try:
            # print("packet sniff time ", packet.sniff_time, type(packet.sniff_time), fix_sniff_tz(packet.sniff_time), src, dst)
            insert += f"('{fix_sniff_tz(packet.sniff_time)}', '{src}', '{dst}', '{mac}', '{packet.length}', '{proto}', '{ext}'), "
        except Exception as e:
            log.error("Unexpected error on insert:", exc_info = e)
            sys.exit(-1)

    if insert != "INSERT INTO packets (time, src, dst, mac, len, proto, ext) VALUES ":
        insert = insert[:-2]
        insert += ";"
        cur.execute(insert)

    # commit the new records and close db connection
    conn.commit()
    cur.close()
    conn.close()    
    log.info(f"Captured {str(len(packets))} packets this tick")


def QueuedCommit(packet):
    # commit packets to the database in COMMIT_INTERVAL second intervals

    now = datetime.utcnow()
    global timestamp
    global queue

    # first packet in new queue
    if timestamp == 0:
        timestamp = now

    queue.append(packet)

    # time to commit to db
    if (now - timestamp).total_seconds() > COMMIT_INTERVAL:
        if len(queue) > 0:
            DatabaseInsert(queue)
        queue = []
        timestamp = 0



if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--config', dest="config", type=str, help="Path to config file, default is %s" % CONFIG_PATH)
    parser.add_argument('--interface', dest="interface", type=str, help="Interface to listen to")
    parser.add_argument('--interval', dest="interval", type=float, help="Commit interval in seconds")
    parser.add_argument('--debug', dest='debug', action='store_true')
    args = parser.parse_args()

    if args.debug:
        log.setLevel(logging.DEBUG)
        
    INTERFACE = None
    CONFIG_PATH = args.config if args.config else CONFIG_PATH

    log.debug(f"Loading config from {CONFIG_PATH}")
    CONFIG = configparser.ConfigParser()
    CONFIG.read(CONFIG_PATH)

    if args.interface is not None:
        INTERFACE = args.interface
    elif "capture" in CONFIG and "interface" in CONFIG['capture']:
        INTERFACE = CONFIG['capture']['interface']
    else:
        log.error(parser.print_help())
        sys.exit(-1)

    if args.interval is not None:
        COMMIT_INTERVAL = args.interval
    elif "capture" in CONFIG and "interval" in CONFIG['capture']:
        COMMIT_INTERVAL = float(CONFIG['capture']['interval'])
    else:
        log.error(parser.print_help())
        sys.exit(-1)    


    log.info(f"Setting capture interval {COMMIT_INTERVAL} ")
    log.info(f"Setting up to capture from {INTERFACE}")
    capture = pyshark.LiveCapture(interface=INTERFACE, bpf_filter='udp or tcp')

    if log.level == logging.DEBUG:
        capture.set_debug()

    log.info("Starting capture")

    capture.apply_on_packets(QueuedCommit)  # timeout=30)
    capture.close()
