# -*- coding: utf-8 -*-

# input params
# path to the config file, see pullStatsConfig.json

import pandas as pd
import boto3, json, datetime, sys, os, getopt, ConfigParser
from collections import defaultdict
from optparse import OptionParser

def getCmdMetrics():
    metrics = [
        'GetTypeCmds', 'HashBasedCmds','HyperLogLogBasedCmds', 'KeyBasedCmds', 'ListBasedCmds', 'SetBasedCmds', 
        'SetTypeCmds', 'SortedSetBasedCmds', 'StringBasedCmds', 'StreamBasedCmds']
    return metrics

def getMetrics():
    metrics = [
        'CurrItems', 'BytesUsedForCache', 'CacheHits', 'CacheMisses', 'CurrConnections',
        'NetworkBytesIn', 'NetworkBytesOut', 'NetworkPacketsIn', 'NetworkPacketsOut', 
        'EngineCPUUtilization', 'Evictions', 'ReplicationBytes', 'ReplicationLag',]
    return metrics

def calc_expiry_time(expiry):
    """Calculate the number of days until the reserved instance expires.
    Args:
        expiry (DateTime): A timezone-aware DateTime object of the date when
            the reserved instance will expire.
    Returns:
        The number of days between the expiration date and now.
    """
    return (expiry.replace(tzinfo=None) - datetime.datetime.utcnow()).days

def getClustersInfo(session):
    """Calculate the running/reserved instances in ElastiCache.
    Args:
        session (:boto3:session.Session): The authenticated boto3 session.
    Returns:
        A dictionary of the running/reserved instances for ElastiCache nodes.
    """
    conn = session.client('elasticache')
    results = {
        'elc_running_instances': {},
        'elc_reserved_instances': {},
    }
    
    paginator = conn.get_paginator('describe_cache_clusters')
    page_iterator = paginator.paginate(ShowCacheNodeInfo=True)
    # Loop through running ElastiCache instance and record their engine,
    # type, and name.
    for page in page_iterator:
        for instance in page['CacheClusters']:
            if instance['CacheClusterStatus'] == 'available' and instance['Engine'] == 'redis':
                clusterId = instance['CacheClusterId']

                results['elc_running_instances'][clusterId] = instance

    paginator = conn.get_paginator('describe_reserved_cache_nodes')
    page_iterator = paginator.paginate()

    # Loop through active ElastiCache RIs and record their type and engine.
    for page in page_iterator:
        for reserved_instance in page['ReservedCacheNodes']:
            if reserved_instance['State'] == 'active' and reserved_instance['ProductDescription'] == 'redis':
                instance_type = reserved_instance['CacheNodeType']

                # No end datetime is returned, so calculate from 'StartTime'
                # (a `DateTime`) and 'Duration' in seconds (integer)
                expiry_time = reserved_instance[
                    'StartTime'] + datetime.timedelta(
                        seconds=reserved_instance['Duration'])
                results['elc_reserved_instances'][(instance_type)] = {
                    'count': reserved_instance['CacheNodeCount'],
                    'expiry_time': calc_expiry_time(expiry=expiry_time)
                }

    return results    

def writeCmdMetric(clusterId, node, metric):
    """Write Redis commands metrics to the file
    Args:
        ClusterId, node and metric to write
    Returns:
    """
    response = cw.get_metric_statistics(
        Namespace='AWS/ElastiCache',
        MetricName=metric,
        Dimensions=[
            {'Name': 'CacheClusterId', 'Value': clusterId},
            {'Name': 'CacheNodeId', 'Value': node}
        ],
        StartTime=(datetime.datetime.now() - datetime.timedelta(days=7)).isoformat(),
        EndTime=datetime.datetime.now().isoformat(),
        Period=3600,
        Statistics=['Maximum']
    )

    max = 0
    for rec in response['Datapoints']:
        if(rec['Maximum'] > max):
            max = rec['Maximum']
    
    f.write("%s," % max)
    
def writeMetric(clusterId, node, metric):
    """Write node related metrics to file
    Args:
        ClusterId, node and metric to write
    Returns:
    """
    response = cw.get_metric_statistics(
        Namespace='AWS/ElastiCache',
        MetricName=metric,
        Dimensions=[
            {'Name': 'CacheClusterId', 'Value': clusterId},
            {'Name': 'CacheNodeId', 'Value': node}
        ],
        StartTime=(datetime.datetime.now() - datetime.timedelta(days=7)).isoformat(),
        EndTime=datetime.datetime.now().isoformat(),
        Period=3600,
        Statistics=['Maximum']
    )

    max = 0
    for rec in response['Datapoints']:
        if(rec['Maximum'] > max):
            max = rec['Maximum']
    
    f.write("%s," % max)

def writeHeaders(outputFile):
    """Write file headers to the csv file
    Args:
    Returns:
    """
    outputFile.write('ClusterId,NodeId,NodeType,Region,')
    for metric in getMetrics():
        outputFile.write('%s (max over last week),' % metric)
    for metric in getCmdMetrics():
        outputFile.write('%s (peak last week / hour),' % metric)
    outputFile.write("\r\n")

def writeClusterInfo(outputFile, clustersInfo):
    """Write all the data gathered to the file
    Args:
        The cluster information dictionary
    Returns:
    """
    for instanceId, instanceDetails in clustersInfo['elc_running_instances'].items():
        for node in instanceDetails.get('CacheNodes'):
            print("Getting node % s details" %(instanceDetails['CacheClusterId']))
            if 'ReplicationGroupId' in instanceDetails:
                outputFile.write("%s," % instanceDetails['ReplicationGroupId'])
            else:
                outputFile.write(",")

            outputFile.write("%s," % instanceId)
            outputFile.write("%s," % instanceDetails['CacheNodeType'])
            outputFile.write("%s," % instanceDetails['PreferredAvailabilityZone'])
            for metric in getMetrics():
                writeMetric(instanceId, node.get('CacheNodeId'), metric)
            for metric in getCmdMetrics():
                writeCmdMetric(instanceId, node.get('CacheNodeId'), metric)
            outputFile.write("\r\n")
    
    outputFile.close()


def processClusterInfo(outputFilePath):
    """Load the information and sort the results according ClusterId
    Args:
        Takes the outputfile
    Returns:
    """
    outputDF = pd.read_csv(outputFilePath)
    outputDF.sort_values(by=['ClusterId'], inplace=True)
    outputDF.to_csv(outputFilePath)

def writeReservedInstances(outputFile, clustersInfo):
    outputFile.write("\r\n")
    outputFile.write("\r\n")
    outputFile.write("###Reserved Instances")
    outputFile.write("\r\n")
    outputFile.write("Instance Type, Count, Remaining Time (days)")
    outputFile.write("\r\n")
    
    for instanceId, instanceDetails in clustersInfo['elc_reserved_instances'].items():
        outputFile.write("%s," % instanceId)
        outputFile.write("%s," % instanceDetails['count'])
        outputFile.write("%s," % instanceDetails['expiry_time'])
        outputFile.write("\r\n")

def writeCosts(outputFile, session):
    outputFile.write("\r\n")
    outputFile.write("costs\r\n")
    pr = session.client('ce')
    now = datetime.datetime.now()
    start = (now - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
    end = now.strftime('%Y-%m-%d')
    pricingData = pr.get_cost_and_usage(TimePeriod={'Start': start, 'End':  end}, 
        Granularity='MONTHLY',
        Filter={"And": [{"Dimensions": {'Key': 'REGION', 'Values': [session.region_name]}},
        {"Dimensions": {'Key': 'SERVICE', 'Values':['Amazon ElastiCache']}}]},
        Metrics=['UnblendedCost'])

    costs = 0
    for res in pricingData['ResultsByTime']:
        costs = costs + float(res['Total']['UnblendedCost']['Amount'])    

    outputFile.write("####Total costs per month####  %s" % costs)
    outputFile.close()
    print('###Done###')


def processAWSAccount(session, outputFile, outputFilePath):
    print('Grab a coffee this script takes a while...')
    print('Writing Headers')
    writeHeaders(outputFile)
    print('Gathring data...')
    clustersInfo = getClustersInfo(session)
    writeClusterInfo(outputFile, clustersInfo)
    processClusterInfo(outputFilePath)
    outputFile = open(outputFilePath, "a")
    writeReservedInstances(outputFile, clustersInfo)
    writeCosts(outputFile, session)

def main():
    parser = OptionParser()
    parser.add_option("-c", "--config", dest="configFile",
                  help="Location of configuration file", metavar="FILE")
    
    (options, args) = parser.parse_args()

    config = ConfigParser.ConfigParser()
    config.read(options.configFile)

    for section in config.sections():

        region = config.get(section, 'region')
        accessKey = config.get(section, 'aws_access_key_id')
        secretKey = config.get(section, 'aws_secret_access_key')

        outputFilePath = "%s-%s.csv" % (section, region)

        outfile = open(outputFilePath,"w+")

        # connect to ElastiCache 
        # aws key, secret and region
        session = boto3.Session(
            aws_access_key_id=accessKey, 
            aws_secret_access_key=secretKey,
            region_name=region)

        cw = session.client('cloudwatch')
        processAWSAccount(session, outfile, outputFilePath)



if __name__ == "__main__" :
    main()
