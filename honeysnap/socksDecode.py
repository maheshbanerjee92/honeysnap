################################################################################
# (c) 2006, The Honeynet Project
#   Author: Scott Buchan sbuchan@hush.com 
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
################################################################################

# $Id: $

import pcap
import dpkt
import base
import socket
import sys
import time
import struct
from util import make_dir  
from singletonmixin import HoneysnapSingleton
from datetime import datetime

class SocksDecode(base.Base):
    
    def __init__(self, pcapObj, hp): 
        hs = HoneysnapSingleton.getInstance()
        options = hs.getOptions()
        self.p = pcapObj
        self.outdir = ""
        self.hp = hp
        self.dataFound = False  
        self.tf = options['time_convert_fn']     
        self.cdList = [90,91,92,93]
        self.commandCode = {
            90:'Request granted', 
            91:'Request rejected or failed',
            92:'Request rejected: SOCKS server cannot connect to identd on the client',
            93:'Request rejected: Client program and identd report different user-ids'
        }   
        self.replyCode = {
            0:'Succeeded', 
            1:'General socks server failure',
            2:'Connection not allowed by ruleset',
            3:'Network unreachable',
            4:'Host unreachable',
            5:'Connection refused',
            6:'TTL expired',
            7:'Command not supported',
            8:'Address type not supported'
        }   
        self.requestCommand = [1,2,3]

    def setOutdir(self, dir):
        make_dir(dir)
        self.outdir = dir
        self.fp = open(dir + "/socks.txt", "w")
        
    def start(self):
        """Iterate over a pcap object"""
        for ts, buf in self.p:
            self.packetHandler(ts, buf)
        if not self.dataFound:   
            self.doOutput('No SOCKS traffic found\n')
        else: 
            self.doOutput('SOCKS data seen and written to socks.txt\n')
        self.fp.close()
            
    def packetHandler(self, ts, buf):
        """Process a pcap packet buffer"""
        try:
            pkt = dpkt.ethernet.Ethernet(buf)
            subpkt = pkt.data
            if type(subpkt) != type(dpkt.ip.IP()):
                # skip non IP packets
                return
            proto = subpkt.p
            shost = socket.inet_ntoa(subpkt.src)
            dhost = socket.inet_ntoa(subpkt.dst)
        except dpkt.Error:
            return
        try:
            if proto == socket.IPPROTO_TCP or proto == socket.IPPROTO_UDP:
                tcp = subpkt.data
                dport = tcp.dport
                sport = tcp.sport
                payload = tcp.data
                #TODO Not sure what the max length should be
                if len(payload) >= 8 and len(payload) <= 60 :
                    #TODO This needs work since I have no udp test data to play with
                    if proto == socket.IPPROTO_UDP and len(payload) >= 10:
                        if self.hp == dhost:
                            self.writeUDPConnection(ts, shost, sport, dhost, dport, payload)
                        return
                    # Get socks version and command code
                    vn, cd = struct.unpack("!BB", payload[0:2])
                    # Check request
                    if vn == 4 and cd == 1:
                        if self.hp == dhost:
                            self.writeS4Connection(vn, ts, shost, sport, dhost, dport, payload)
                        return
                    # Check reply
                    if vn == 0 and cd in self.cdList:
                        if self.hp == shost:
                            version = 4
                            self.writeConnectionReply(version, ts, shost, sport, dhost, dport, payload)  
                        return
                    # Check request
                    if vn == 5 and cd in self.requestCommand and self.hp == dhost:                        
                        self.writeS5Connection(vn, ts, shost, sport, dhost, dport, payload)
                        return
                    # Check reply
                    if vn == 5 and cd in self.replyCode and self.hp == shost:
                        self.writeConnectionReply(vn, ts, shost, sport, dhost, dport, payload)  
                        return
        except dpkt.Error:
            return
       
    def writeUDPConnection(self, ts, shost, sport, dhost, dport, payload):
        self.dataFound = True 
        version = 5
        rsv, frag, atyp, ip1, ip2, ip3, ip4, port = struct.unpack("!HBBBBBBH", payload[0:10])
        ipAddress = self.createIp(ip1, ip2, ip3, ip4)
        if not ipAddress:
            return
        source = shost + ':' + str(sport)
        dest = ipAddress + ':' + str(port)
        socksServer = dhost + ':' + str(dport)
        out = '%s : S%s: %s -> %s -> %s\n' %(self.tf(ts), str(version), source, socksServer, dest)
        self.fp.write(out)
 
    def writeS5Connection(self,version, ts, shost, sport, dhost, dport, payload):
        self.dataFound = True
        data = []
        # Need to determine address type
        ver, cmd, rsv, atyp, size = struct.unpack("!BBBBB", payload[0:5])
        if atyp == 1:
            vn, cd, rsv, atyp, ip1, ip2, ip3, ip4, port = struct.unpack("!BBBBBBBBH", payload[0:10])
            targetString = self.createIp(ip1, ip2, ip3, ip4)
            if not targetString:
                return
            target = targetString + ':' + str(port)
        elif atyp == 3:
            # Create unpack string
            unpackString = '!BBBBB'
            for i in range(size):
                unpackString += 'B'
            # Add for port number
            unpackString += 'H'
            payloadSize = 7 + size    
            data = struct.unpack(unpackString, payload[0:payloadSize])
            targetSlice = 5 + size
            targetData = data[5:targetSlice]
            targetString = ''
            for s in targetData:
                targetString += chr(s)
            targetPort = str(data[len(data)-1])
            target = targetString + ':' + targetPort
        else:
            # unknown 
            return
        source = shost + ':' + str(sport)
        socksServer = dhost + ':' + str(dport)
        out = '%s : S%s: %s: %s -> %s -> %s\n' \
            %(self.tf(ts), str(version), atyp, source, socksServer, target)
        self.fp.write(out)                    
                            
    def writeS4Connection(self, version, ts, shost, sport, dhost, dport, payload):
        self.dataFound = True
        vn, cd, port, ip1, ip2, ip3, ip4 = struct.unpack("!BBHBBBB", payload[0:8])
        ipAddress = self.createIp(ip1, ip2, ip3, ip4)
        if not ipAddress:
            return
        source = shost + ':' + str(sport)
        dest = ipAddress + ':' + str(port)
        socksServer = dhost + ':' + str(dport)
        out = '%s : S%s: %s -> %s -> %s\n' %(self.tf(ts), str(version), source, socksServer, dest)
        self.fp.write(out)
           
    def writeConnectionReply(self, version, ts, shost, sport, dhost, dport, payload):
        ''' Reply is from the socks server to the client. '''
        self.dataFound = True 
        if version == 4:
            vn, cd, port, ip1, ip2, ip3, ip4 = struct.unpack("!BBHBBBB", payload[0:8])
            reply = self.commandCode[cd]
        else:
            vn, cd, rsv, atyp, ip1, ip2, ip3, ip4, port = struct.unpack("!BBBBBBBBH", payload[0:10])
            reply = self.replyCode[cd]
        # These fields are ignored and do not always contain correct ip                
        ipAddress = self.createIp(ip1, ip2, ip3, ip4)
        if not ipAddress:
            return
        #reply = self.commandCode[cd]
        sockServer = shost + ':' + str(sport)
        # I don't think this should be included - fields are ignored
        #dest = ipAddress + ':' + str(port)
        client = dhost + ':' + str(dport)
        out = '%s : S%s: %s: %s -> %s \n' %(self.tf(ts), str(version), reply, client, sockServer)
        self.fp.write(out)
        #print '%s : S4: %s: %s -> %s -> %s' %(self.tf(ts), reply, client, sockServer, dest)
        
    def createIp(self, ip1, ip2, ip3, ip4):
        if ip1 < 0 or ip1 > 255:
            return None
        elif ip2 < 0 or ip2 > 255:
            return None
        elif ip3 < 0 or ip3 > 255:
            return None
        elif ip4 < 0 or ip4 > 255:
            return None
        ip = str(ip1) + '.' + str(ip2) + '.' +  str(ip3) + '.' + str(ip4)
        return ip
                


