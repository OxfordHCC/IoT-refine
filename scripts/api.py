#! /usr/bin/env python3

from flask import Flask, request, jsonify, make_response
from flask_restful import Resource, Api
import json
import re
import sys
import os
import traceback
import copy
from datetime import datetime
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db"))
import databaseBursts

DB_MANAGER = databaseBursts.dbManager() #for running database queries
app = Flask(__name__) #initialise the flask server
api = Api(app) #initialise the flask server
ID_POINTER = 0 #so we know which packets we've seen (for caching)
_impact_cache = dict() #for building and caching impacts
geos = dict() #for building and caching geo data
lastDays = 0 #timespan of the last request (for caching)

#=============
#api endpoints

#return aggregated data for the given time period (in days, called by refine)
class Refine(Resource):
    def get(self, days):
        try:
            response = make_response(jsonify({"bursts": GetBursts(days), "macMan": MacMan(), "manDev": ManDev(), "impacts": GetImpacts(days), "usage": GenerateUsage()}))
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except:
            print("Unexpected error:", sys.exc_info())
            traceback.print_exc()
            sys.exit(-1)                    

#get the mac address, manufacturer, and custom name of every device
class Devices(Resource):
    def get(self):
        return jsonify({"macMan": MacMan(), "manDev": ManDev()})

#set the custom name of a device with a given mac
class SetDevice(Resource):
    def get(self, mac, name):
        mac_format = re.compile('^(([a-fA-F0-9]){2}:){5}[a-fA-F0-9]{2}$')
        if mac_format.match(mac) is not None:
            DB_MANAGER.execute("UPDATE devices SET name=%s WHERE mac=%s", (name, mac))
            return jsonify({"message": "Device with mac " + mac + " now has name " + name})
        else:
            return jsonify({"message": "Invalid mac address given"})

#return all traffic bursts for the given time period (in days)
class Bursts(Resource):
    def get(self, days):
        return jsonify(GetBursts(days))

#return all impacts for the given time period (in days)
class Impacts(Resource):
    def get(self, days):
        return jsonify(GetImpacts(days))

#================
#internal methods

#return a dictionary of mac addresses to manufacturers
def MacMan():
    macMan = dict()
    devices = DB_MANAGER.execute("SELECT * FROM devices", ())
    for device in devices:
        mac,manufacturer,_ = device
        macMan[mac] = manufacturer
    return macMan

#return a dictionary of mac addresses to custom device names
def ManDev():
    manDev = dict()
    devices = DB_MANAGER.execute("SELECT * FROM devices", ())
    for device in devices:
        mac,_,name = device
        manDev[mac] = name
    return manDev

#get geo data for an ip
def GetGeo(ip):
    print("Get Geo ", ip)
    try:
        lat,lon,c_code,c_name = DB_MANAGER.execute("SELECT lat, lon, c_code, c_name FROM geodata WHERE ip=%s LIMIT 1", (ip,), False)
        geo = {"latitude": lat, "longitude": lon, "country_code": c_code, "companyName": c_name}
        return geo
    except:
        geo = {"latitude": 0, "longitude": 0, "country_code": 'XX', "companyName": 'unknown'}
        return geo

#get bursts for the given time period (in days)
def GetBursts(days):
    bursts = DB_MANAGER.execute("SELECT MIN(time), MIN(mac), burst, MIN(categories.name) FROM packets JOIN bursts ON bursts.id = packets.burst JOIN categories ON categories.id = bursts.category WHERE time > (NOW() - INTERVAL %s) GROUP BY burst ORDER BY burst", ("'" + str(days) + " DAY'",))
    result = []
    epoch = datetime(1970, 1, 1, 0, 0)
    for burst in bursts:
        unixTime = int((burst[0] - epoch).total_seconds() * 1000.0)
        device = burst[1]
        category = burst[3]
        result.append({"value": unixTime, "category": category, "device": device })
    return result

#get impact (traffic) of every device/external ip combination for the given time period (in days)
def GetImpacts(days):
    global geos, ID_POINTER, lastDays, _impact_cache
    
    print("GetImpacts: days::", days, " ID>::", ID_POINTER, " lastDays::", lastDays)

    #we can only keep the cache if we're looking at the same packets as the previous request
    if days is not lastDays:
        print("ResetImpactCache()")
        ResetImpactCache()

    impacts = copy.deepcopy(_impact_cache) # shallow copy
    idp = ID_POINTER

    #get all packets from the database (if we have cached impacts from before, then only get new packets)
    packets = DB_MANAGER.execute("SELECT * FROM packets WHERE id > %s AND time > (NOW() - INTERVAL %s) ORDER BY id", (str(idp), "'" + str(days) + " DAY'"))
    result = []
    local_ip_mask = re.compile('^(192\.168|10\.|255\.255\.255\.255).*') #so we can filter for local ip addresses

    for packet in packets:
        #determine if the src or dst is the external ip address
        pkt_id, pkt_time, pkt_src, pkt_dst, pkt_mac, pkt_len, pkt_proto, pkt_burst = packet
        
        ip_src = local_ip_mask.match(pkt_src) is not None
        ip_dst = local_ip_mask.match(pkt_dst) is not None
        ext_ip = None
        
        if (ip_src and ip_dst) or (not ip_src and not ip_dst):
            continue #shouldn't happen, either 0 or 2 internal hosts
        
        #remember which ip address was external
        elif ip_src:
            ext_ip = pkt_dst
        else:
            ext_ip = pkt_src
        
        #make sure we have geo data, then update the impact
        if ext_ip not in geos:
            geos[ext_ip] = GetGeo(ext_ip)

        # print("UpdateImpact ", pkt_mac, ext_ip, pkt_len)
        UpdateImpact(impacts, pkt_mac, ext_ip, pkt_len)

        if idp < pkt_id:
            idp = pkt_id

    #build a list of all device/ip impacts and geo data
    for ip,geo in geos.items():
        for mac,_ in ManDev().items():
            item = geo.copy() # emax added .copy here() this is so gross
            item['impact'] = GetImpact(mac, ip, impacts)
            # print("Calling getimpact mac::", mac, " ip::", ip, 'impact result ', item['impact']);            
            item['companyid'] = ip
            item['appid'] = mac
            if item['impact'] > 0:
                result.append(item)

    # commit these all at once to the globals #
    _impact_cache = impacts    
    lastDays = days
    ID_POINTER = idp
    
    # print("result ", json.dumps(result))
    return result #shipit

#setter method for impacts
def UpdateImpact(impacts, mac, ip, impact):
    if mac in impacts:
        print("updateimpact existing mac ", mac)
        if ip in impacts[mac]:
            print("updateimpact existing ip, updating impact for mac ", mac, " ip ", ip, " impact: ", impacts[mac][ip])        
            impacts[mac][ip] += impact
        else:
            print("updateimpact no existing ip for mac ", mac, " ip ", ip, " impact: ", impact)                    
            impacts[mac][ip] = impact #impact did not exist
    else:
        print("updateimpact unknown mac, creating new entry for  ", mac, ip)        
        impacts[mac] = dict()
        impacts[mac][ip] = impact #impact did not exist

#getter method for impacts
def GetImpact(mac, ip, impacts=_impact_cache):
    if mac in impacts:
        if ip in impacts[mac]:
            return impacts[mac][ip]
        else:            return 0 #impact does not exist
    else:
        return 0 #impact does not exist

#clear impact dictionary and packet id pointer
def ResetImpactCache():
    global _impacts_cache, ID_POINTER
    _impacts_cache = dict()
    ID_POINTER = 0

#generate fake usage for devices (a hack so they show up in refine)
def GenerateUsage():
    usage = []
    counter = 1
    for mac in MacMan():
        usage.append({"appid": mac, "mins": counter})
        counter += 1
    return usage

#=======================
#main part of the script
if __name__ == '__main__':
    #Register the API endpoints with flask
    api.add_resource(Refine, '/api/refine/<days>')
    api.add_resource(Devices, '/api/devices')
    api.add_resource(Bursts, '/api/bursts/<days>')
    api.add_resource(Impacts, '/api/impacts/<days>')
    api.add_resource(SetDevice, '/api/setdevice/<mac>/<name>')

    #Start the flask server
    app.run(port=4201, threaded=True, host='0.0.0.0')
