"""
Main methods for binning packets into bursts and categorising packets
"""
import requests, os, pickle, json, sys

import predictions
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db"))
import databaseBursts # pylint: disable=C0413, E0401

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "macHelpers"))
import macHelpMethods # pylint: disable=C0413, E0401

FILE_PATH = os.path.dirname(os.path.abspath(__file__))

DB_MANAGER = databaseBursts.dbManager()

def packetBurstification(data=False):
    """ Get all packets not in bursts and assign them to a new burst """
    # Get packets not in bursts
    
    unBinned = DB_MANAGER.getNoBurst()

    allBursts = []  # List of list of ids
    allIds = set()  # Set of ids considered already
    nextBurst = []  # Ids to go in next burst

    with open(os.path.join(FILE_PATH, 'dicts.json'), 'r') as f:
        config = json.load(f)

    # Get ids of all the packets we want in bursts
    for counter, row in enumerate(unBinned):
        id = row[0]
        mac = row[4]

        dev = macHelpMethods.getDeviceFromMac(mac, data)

        
        try:
            burstTimeInterval = int( config["burstTimeIntervals"][dev] )
        except KeyError:
            burstTimeInterval = int( config["burstTimeIntervals"]["Unknown"] )
        
        if id not in allIds:
            
            nextBurst = [id]
            allIds.add(id)

            currentTime = row[1]

            #print(id)
            #print(type(id))

            try:
                for otherRow in unBinned[counter+1:]:
                    if otherRow[0] not in allIds:

                        if otherRow[4] == mac and burstTimeInterval > (otherRow[1] - currentTime).total_seconds():
                            
                            # If less than TIME_INTERVAL away, add to this burst
                            nextBurst.append(otherRow[0])
                            # Don't need to look at this one again, it's in this potential burst
                            allIds.add(otherRow[0])

                            currentTime = otherRow[1]

                        elif otherRow[4] == mac and burstTimeInterval < (otherRow[1] - currentTime).total_seconds():
                            
                            allBursts.append(nextBurst)
                            # If same device, but too far away, we can stop, there won't be another burst here
                            break
                            # Can't add to considered, might be the start of the next burst

                        elif otherRow[4] != mac:
                            continue
                            # If it's a different device, we can't say anything at this point
            except IndexError:
                continue     

        else:
            # If we've considered it we know it was within interval of another packet and so
            # it's either a valid burst or part of one that is too short
            continue

    allBursts.append(nextBurst)

    # Add each new burst, and add all the packet rows to it
    for burst in allBursts:
        newBurstId = DB_MANAGER.insertNewBurst()
        DB_MANAGER.updatePacketBurstBulk(burst, [newBurstId for _ in range(len(burst))])
            


def burstPrediction(data=False):
    """
    Predict a category for each burst, or don't assign if there is no prediction
    """
    unCat = DB_MANAGER.getNoCat()

    #print(unCat)

    with open(os.path.join(FILE_PATH, 'dicts.json'), 'r') as f:
        config = json.load(f)
        cutoffs = config["burstNumberCutoffs"]
    
    

    for burst in unCat:
        
        rows = DB_MANAGER.getRowsWithBurst(burst[0])

        #print(burst, rows)

        if len(rows) == 0:
            continue

        device = macHelpMethods.getDeviceFromMac(rows[0][4], data)

        if "Echo" in device and len(rows) > cutoffs["Echo"]:
            category = predictions.predictEcho(rows)
        elif device == "Hue" and len(rows) > cutoffs[device]:
            category = predictions.predictHue(rows)
        else:
            category = predictions.predictOther(rows)

        


        # Get the id of this category, and add if necessary
        newCategoryId = DB_MANAGER.addOrGetCategoryNumber(category)

        # Update the burst with the name of the new category, packets already have a reference to the burst
        DB_MANAGER.updateBurstCategory(burst[0], newCategoryId)



    #88:71:e5:e9:9e:6c