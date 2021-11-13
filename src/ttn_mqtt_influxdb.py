#!/usr/bin/env python3

"""A MQTT to InfluxDB Bridge

This script receives MQTT data and saves those to InfluxDB.

"""

import re
from typing import NamedTuple

import paho.mqtt.client as mqtt
from influxdb import InfluxDBClient
import json
import time
import base64
import sys
import os

import logging
log=logging.getLogger(__name__)

# get environment config from env vars if set else defaults
INFLUXDB_ADDRESS = os.environ.get('INFLUXDB_ADDRESS', '127.0.0.1')
INFLUXDB_USER = os.environ.get('INFLUXDB_USER', 'msgproc')
INFLUXDB_PASSWORD = os.environ.get('INFLUXDB_PASSWORD', 'msgproc01')
TTN_TENANT = os.environ.get('TTN_TENANT', 'ttn')
MQTT_ADDRESS = os.environ.get('MQTT_ADDRESS', 'eu1.cloud.thethings.network')
MQTT_PORT = os.environ.get('MQTT_PORT', 1883)

class TTNConnector:

    def __init__(self, app:str, apikey:str):
        self._appname = app
        self._ttnusername = app+"@"+os.environ.get('TTN_TENANT', 'ttn')
        self._influxdb_client = None
        self._mqtt_client = None
        # can pass app as None to create test instance
        if app is not None:
            self._influxdb_client = TTNConnector._init_influxdb_database(os.environ.get('INFLUXDB_ADDRESS', 'localhost'), 
                                        os.environ.get('INFLUXDB_USER', 'msgproc'), os.environ.get('INFLUXDB_PASSWORD', 'msgproc01'), 
                                        app)
            self._mqtt_client = mqtt.Client(app)
            self._mqtt_client.username_pw_set(self._ttnusername, apikey)
            self._mqtt_client.on_connect = self.on_connect
            self._mqtt_client.on_message = self.on_message
            self._mqtt_client.connect(os.environ.get('MQTT_ADDRESS', 'eu1.cloud.thethings.network'), int(os.environ.get('MQTT_PORT', 1883)))

    def loop_forever(self):
        self._mqtt_client.loop_forever()

    @staticmethod
    def getMaxRSSI(gateways):
        bestRSSI=-999
        for gw in gateways:
            rssi = gw.get('rssi',-150)
            if(rssi>bestRSSI):
                bestRSSI=rssi
        return bestRSSI

    @staticmethod
    def mapDR2SF(dr:int)->int:
        sflist=[ 12, 11, 10, 9, 8, 7]
        if dr<0 or dr>len(sflist):
            return dr
        return sflist[dr]

    def _parse_mqtt_message_ttn(self, topic, mqtt_data):
        msg = json.loads(mqtt_data)
        log.info("Incoming from topic %s msg %s",topic, msg)

        devEui = msg.get('end_device_ids', {}).get('device_id', 'UNKNOWN')

        self._send_sensor_data_to_influxdb(devEui, 'ulFrequency', float(msg.get('uplink_message', {}).get('settings',{}).get('frequency',0)))
        self._send_sensor_data_to_influxdb(devEui, 'ulSF', TTNConnector.mapDR2SF(msg.get('uplink_message', {}).get('settings',{}).get('data_rate_index',0)))
        self._send_sensor_data_to_influxdb(devEui, 'rssi', float(TTNConnector.getMaxRSSI(msg.get('uplink_message', {}).get('rx_metadata', []))))
        self._send_sensor_data_to_influxdb(devEui, 'nbGateways', float(len(msg.get('uplink_message', {}).get('rx_metadata', []))))

        # depending on type of message:
        # JOIN : log to lora activity DB
        # TODO
        datas = msg.get('uplink_message',{}).get('decoded_payload',None)
        if datas is None:
            # UL data: Wyres app-generic TLV format
            #Payload decoding
            tlvlist = TTNConnector.decoder(base64.b64decode(msg.get('uplink_message',{}).get('frm_payload','')).hex())
            # convert and clean
            datas = {}
            for tlv in tlvlist:
                if(tlv.decodedKey!=''):
                    datas[tlv.decodedKey] = data.decodedValue

        for dk, dv in datas.items():
            # we only put numbers in our DB
            if isinstance(dv, float) or isinstance(dv, int):
                self._send_sensor_data_to_influxdb(devEui,dk,float(dv))
            else:
                log.info("ignoring non number value %s=%s", dk, dv)

    def on_connect(self, client, userdata, flags, rc):
        """ The callback for when the client receives a CONNACK response from the server."""
        if rc!=0:
            log.warning('Connect NOK : {}',rc)
            self._mqtt_client = None
            return
        # we want uplink messages
        topic = "v3/{}/devices/+/up".format(self._ttnusername)
        log.info('Connected OK - subscribing to topic %s', topic)
        client.subscribe(topic)


    def on_message(self, client, userdata, msg):
        """The callback for when a PUBLISH message is received from the server."""
        self._parse_mqtt_message_ttn(msg.topic, msg.payload)



    def _send_sensor_data_to_influxdb(self, deveui,measurementKey,value):
        json_body = [
            {
                'measurement': measurementKey,
                'tags': {
                    'location': deveui,
                    'displayName': TTNConnector.getDeviceNameByID(deveui)
                },
                'fields': {
                    'value': value
                }
            }
        ]
        log.info("writing to db: %s",json_body)
        if self._influxdb_client is not None:
            self._influxdb_client.write_points(json_body)

    @staticmethod
    def _init_influxdb_database(db_host, db_user, db_pass, db_name):
        log.info("connecting to influxdb on host %s with user %s and pass %s", db_host, db_user, db_pass)
        influxdb_client = InfluxDBClient(host=db_host, port=8086, username=db_user, password=db_pass, database=None)
        databases = influxdb_client.get_list_database()
        log.info("checking for db %s", db_name)
        if len(list(filter(lambda x: x['name'] == db_name, databases))) == 0:
            log.info("no such db %s, trying to create it", db_name)
            influxdb_client.create_database(db_name)
        influxdb_client.switch_database(db_name)
        return influxdb_client

    @staticmethod
    def getDeviceNameByID(devEUI):
        devices={}
        devices['38b8ebe100000001']='Salon'
        devices['38b8ebe100000002']='Cuisine'
        devices['38b8ebe100000003']='Chambre-GM'
        devices['38b8ebe100000004']='Chambre-ML'
        devices['38b8ebe10000000x']='Chambre-PJ'
        devices['38b8ebe100000005']='Balcon'
        devices['38b8ebe100000007']='Cave'
        devices['38b8ebe100000008'] = 'Toilettes'
        devices['38b8ebe000001377'] = 'gpGpsBle'
        if devEUI.lower() in devices:
            return devices[devEUI.lower()]
        else:
            return devEUI.lower()

    @staticmethod
    def decoder(payload):
        print(payload)
        if payload is None or len(payload)<2:
            return []
        returnedList = []
        payloadLength = int(payload[2:4], 16)
        log.info('  '+payload[2:4])
        log.info('payloadLength :'+str(payloadLength))

        i = 4
        while (i < len(payload)):
            tval=0
            lval = 0
            valval = ""
            tval = int(payload[i:i+2],16)
            lval = int(payload[i+2:i+4],16)
    #        print('tag:'+str(tval))
    #        print('length:'+str(lval))

            j = i+4
            while(j<i+4+(lval*2)):
                valval += payload[j:j+2]
                j=j+2
            log.info('tag ['+str(tval)+'] length ['+str(lval)+'] val ['+valval+']')
            i = i + 4 + (lval * 2)

            measurement = TLV(tval,lval,valval)
            measurement.compute()
            measurement.print()
            returnedList.append(measurement)
        return returnedList



class TLV:
    key: int
    length: int
    value: str
    decodedKey : str
    decodedValue : str
    def __init__(self, key,length, value):
        self.key = key
        self.length = length
        self.value = value
        self.decodedKey = ''
        self.decodedValue = -1
    def compute(self):
        if(self.key == 3):
            self.decodedKey = 'temperature'
            temp = self.s16(int(self.invertValue(),16))
            self.decodedValue = temp/100
        if(self.key == 4):
            self.decodedKey = 'pressure'
            self.decodedValue = int(self.invertValue(),16)/100
        if(self.key == 6):
            self.decodedKey = 'light'
            self.decodedValue = int(self.invertValue(),16)
        if(self.key == 7):
            self.decodedKey = 'battery'
            self.decodedValue = int(self.invertValue(), 16) / 1000
        if(self.key == 12):
            self.decodedKey = 'hasMoved'
            self.decodedValue = 1

    def invertValue(self):
        i=len(self.value)
        rtn = ""
        while(i>0):
            rtn += self.value[i-2:i]
            i=i-2
        return rtn

    def print(self):
        if(self.decodedKey != ''):
            print(self.decodedKey+': '+str(self.decodedValue))
        else:
            print(str(self.key)+' not decoded')

    def s16(self,value):
        return -(value & 0x8000) | (value & 0x7fff)


def parseArgs(aa, minArgs:int) -> dict:
    ret = {}
    ai = 0
    curarg = None
    for a in aa:
        if a.startswith('-'):
            if curarg is not None:
                ret[curarg] = True
            curarg = a
        else:
            if curarg is not None:
                ret[curarg] = a
                curarg=None
            else:
                ai+=1
                ret[str(ai)] = a
    # deal with trailing noarg -xx
    if curarg is not None:
        ret[curarg] = True
    if ai < minArgs:
        raise Exception("Require at least "+minArgs+" arguments")
    return ret

def injectEnv(argd:dict, env_mapping:dict) -> None:
    # map any we care about as though they were args IFF they are not already set (command line takes precedence)
    for ek, ak in env_mapping.items():
        ev = os.getenv(ek)
        if ev is not None:
            if argd.get(ak, None) is None:
                argd[ak] = ev

def exit_usage(err):
    print(err)
    print("python3 ttn-msg-proc.py -u <ttn application name> -p <ttn api key>")
    sys.exit(-1)

def main():
    # deal with arguments to set ttn user aka the application name (without the tenant id) and password aka apikey
    # the 'user' is used as the client id and the database name so that this can be run multiple times to deal with multiple applications
    argd = parseArgs(sys.argv, 0)
    injectEnv(argd, { "MQTT_USER":"-u", "MQTT_PASS":"-p"})
    u = argd.get("-u")
    p = argd.get("-p")
    if u is None:
        exit_usage("missing -u username")
    if p is None:
        exit_usage("missing -p apikey")
    log.info("Starting with user %s and password %s", u, p)
    ttn = TTNConnector(u, p)
    ttn.loop_forever()


def test():
    ttn = TTNConnector(None, None)
    ttn._parse_mqtt_message_ttn("v3/smarthouse-22rdk-29600@ttn/devices/lht65-4c84/up",
        '{"end_device_ids":{"device_id":"lht65-4c84","application_ids":{"application_id":"smarthouse-22rdk-29600"},"dev_eui":"A8404163F1834C84","join_eui":"A000000000000100","dev_addr":"260BE2DB"},"correlation_ids":["as:up:01FM722YWZRGCB6F7WA5YQAFZ5","gs:conn:01FM0NXF80SYCQ5AGZN773P69E","gs:up:host:01FM0NXF86XN6J1PY0KGH0C1W2","gs:uplink:01FM722YPDD1MWD9GGAYKGHXAH","ns:uplink:01FM722YPEQ09460S66FVMP19A","rpc:/ttn.lorawan.v3.GsNs/HandleUplink:01FM722YPE81K7NM4PKRWMNB65","rpc:/ttn.lorawan.v3.NsAs/HandleUplink:01FM722YWZJKEC5NDH0R87YRFE"],"received_at":"2021-11-11T08:33:35.136100251Z","uplink_message":{"session_key_id":"AX0AeBbvz47jf6oEZefMQQ==","f_port":2,"f_cnt":191,"frm_payload":"zB8FuQKkAQUmf/8=","decoded_payload":{"BatV":3.103,"Bat_status":3,"Ext_sensor":"Temperature Sensor","Hum_SHT":67.6,"TempC_DS":13.18,"TempC_SHT":14.65},"rx_metadata":[{"gateway_ids":{"gateway_id":"wtc-ttn-0002","eui":"008000000000AA38"},"time":"2021-11-11T08:33:34.909192Z","timestamp":746672804,"rssi":-60,"channel_rssi":-60,"snr":8.5,"uplink_token":"ChoKGAoMd3RjLXR0bi0wMDAyEggAgAAAAACqOBCkpYXkAhoMCN6qs4wGEP6phrkDIKChq8nd/zA=","channel_index":1}],"settings":{"data_rate":{"lora":{"bandwidth":125000,"spreading_factor":11}},"data_rate_index":1,"coding_rate":"4/5","frequency":"868300000","timestamp":746672804,"time":"2021-11-11T08:33:34.909192Z"},"received_at":"2021-11-11T08:33:34.926321616Z","consumed_airtime":"0.823296s","version_ids":{"brand_id":"dragino","model_id":"lht65","hardware_version":"_unknown_hw_version_","firmware_version":"1.8","band_id":"EU_863_870"},"network_ids":{"net_id":"000013","tenant_id":"ttn","cluster_id":"ttn-eu1"}}}')


# create main logger and configure it for everyone
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# create formatter - very annoying can't select to use '{}' type params in log messages!
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', style='%')
#formatter = logging.Formatter(fmt='{asctime} - {levelname} - {message}', style='{')

# create handlers for file logging and console handling
output_file_handler = logging.FileHandler("ttnmsgproc.log", mode='w')
stdout_handler = logging.StreamHandler(sys.stdout)

# add formatter to both handlers
output_file_handler.setFormatter(formatter)
stdout_handler.setFormatter(formatter)

# add both handlers to logger
logger.addHandler(output_file_handler)
logger.addHandler(stdout_handler)

if __name__ == '__main__':
    print('MQTT to InfluxDB bridge')
    main()