#!/usr/bin/env python

import time
import threading
from scapy.all import *
import sys
import socket
import json
import Queue
import interfaces

maxhop = 25

# A request that will trigger the great firewall but will NOT cause
# the web server to process the connection.  You probably want it here

triggerfetch = "GET /?falun+gong HTTP/1.1\r\nhost: www.google.com\r\ntrigger"
triggerfetch2 = "GET /?falun+gong HTTP/1.1\r\nhost: www.google.com\r\n\r\n"

# A couple useful functions that take scapy packets
def isRST(p):
    return (TCP in p) and (p[IP][TCP].flags & 0x4 != 0)

def isICMP(p):
    return ICMP in p

def isTimeExceeded(p):
    return ICMP in p and p[IP][ICMP].type == 11

# A general python object to handle a lot of this stuff...
#
# Use this to implement the actual functions you need.
class PacketUtils:
    def __init__(self, dst=None):
        # Get one's SRC IP & interface
        i = interfaces.interfaces()
        self.src = i[1][0]
        self.iface = i[0]
        self.netmask = i[1][1]
        self.enet = i[2]
        self.dst = dst
        sys.stderr.write("SIP IP %s, iface %s, netmask %s, enet %s\n" %
                         (self.src, self.iface, self.netmask, self.enet))
        # A queue where received packets go.  If it is full
        # packets are dropped.
        self.packetQueue = Queue.Queue(100000)
        self.dropCount = 0
        self.idcount = 0

        self.ethrdst = ""

        # Get the destination ethernet address with an ARP
        self.arp()
        
        # You can add other stuff in here to, e.g. keep track of
        # outstanding ports, etc.
        
        # Start the packet sniffer
        t = threading.Thread(target=self.run_sniffer)
        t.daemon = True
        t.start()
        time.sleep(.1)

    # generates an ARP request
    def arp(self):
        e = Ether(dst="ff:ff:ff:ff:ff:ff",
                  type=0x0806)
        gateway = ""
        srcs = self.src.split('.')
        netmask = self.netmask.split('.')
        for x in range(4):
            nm = int(netmask[x])
            addr = int(srcs[x])
            if x == 3:
                gateway += "%i" % ((addr & nm) + 1)
            else:
                gateway += ("%i" % (addr & nm)) + "."
        sys.stderr.write("Gateway %s\n" % gateway)
        a = ARP(hwsrc=self.enet,
                pdst=gateway)
        p = srp1([e/a], iface=self.iface, verbose=0)
        self.etherdst = p[Ether].src
        sys.stderr.write("Ethernet destination %s\n" % (self.etherdst))


    # A function to send an individual packet.
    def send_pkt(self, payload=None, ttl=32, flags="",
                 seq=None, ack=None,
                 sport=None, dport=80,ipid=None,
                 dip=None,debug=False):
        if sport == None:
            sport = random.randint(1024, 32000)
        if seq == None:
            seq = random.randint(1, 31313131)
        if ack == None:
            ack = random.randint(1, 31313131)
        if ipid == None:
            ipid = self.idcount
            self.idcount += 1
        t = TCP(sport=sport, dport=dport,
                flags=flags, seq=seq, ack=ack)
        ip = IP(src=self.src,
                dst=self.dst,
                id=ipid,
                ttl=ttl)
        p = ip/t
        if payload:
            p = ip/t/payload
        else:
            pass
        e = Ether(dst=self.etherdst,
                  type=0x0800)
        # Have to send as Ethernet to avoid interface issues
        sendp([e/p], verbose=1, iface=self.iface)
        # Limit to 20 PPS.
        time.sleep(.05)
        # And return the packet for reference
        return p


    # Has an automatic 5 second timeout.
    def get_pkt(self, timeout=5):
        try:
            return self.packetQueue.get(True, timeout)
        except Queue.Empty:
            return None

    # The function that actually does the sniffing
    def sniffer(self, packet):
        try:
            # non-blocking: if it fails, it fails
            self.packetQueue.put(packet, False)
        except Queue.Full:
            if self.dropCount % 1000 == 0:
                sys.stderr.write("*")
                sys.stderr.flush()
            self.dropCount += 1

    def run_sniffer(self):
        sys.stderr.write("Sniffer started\n")
        rule = "src net %s or icmp" % self.dst
        sys.stderr.write("Sniffer rule \"%s\"\n" % rule);
        sniff(prn=self.sniffer,
              filter=rule,
              iface=self.iface,
              store=0)

    # Sends the message to the target in such a way
    # that the target receives the msg without
    # interference by the Great Firewall.
    #
    # ttl is a ttl which triggers the Great Firewall but is before the
    # server itself (from a previous traceroute incantation
    def evade(self, target, msg, ttl):
	ret_string = ""
	
	sourcePort = random.randint(2000, 30000)
        PacketUtils.send_pkt(self, payload=None, ttl=32, flags="S",
            seq=None, ack=None,
            sport=sourcePort, dport=80, ipid=None,
            dip=None, debug=False)
        packet = PacketUtils.get_pkt(self, timeout=5)
        if packet is None:
            return ""
        else:
            seq = packet[TCP].seq
            ack = packet[TCP].ack

            PacketUtils.send_pkt(self, payload=None, ttl=32, flags="A", seq=ack,
                                ack=seq+1, sport=sourcePort, dport=80,ipid=None, dip=None, debug=False)

            char_list = list(msg)

            index = 0
            for char in char_list:
                PacketUtils.send_pkt(self, payload=char, ttl=32, flags="A", seq=ack + index,
                                ack=seq+1, sport=sourcePort, dport=80,ipid=None, dip=None, debug=False)
                PacketUtils.send_pkt(self, payload=char, ttl=ttl, flags="A", seq=ack + index,
                                ack=seq+1, sport=sourcePort, dport=80,ipid=None, dip=None, debug=False)
		index += 1

	    data_packet = PacketUtils.get_pkt(self, timeout=5)
	    while data_packet is not None:
		if 'Raw' in data_packet:
			ret_string += str(data_packet['Raw'].load)
		data_packet = PacketUtils.get_pkt(self, timeout=5)

            return ret_string
 
    # Returns "DEAD" if server isn't alive,
    # "LIVE" if teh server is alive,
    # "FIREWALL" if it is behind the Great Firewall
    def ping(self, target):
        # self.send_msg([triggerfetch], dst=target, syn=True)
        sourcePort = random.randint(2000, 30000)
        PacketUtils.send_pkt(self, payload=None, ttl=32, flags="S",
                 seq=None, ack=None,
                 sport=sourcePort, dport=80,ipid=None,
                 dip=None,debug=False)
	
	packet = PacketUtils.get_pkt(self, timeout=5)	
	if packet is None:
		return "DEAD"
	else:
		seq = packet[TCP].seq
		ack = packet[TCP].ack

		PacketUtils.send_pkt(self, payload=None, ttl=32, flags="A",
                	seq=ack, ack=seq+1,
                 	sport=sourcePort, dport=80,ipid=None,
                 	dip=None,debug=False)
        	
		PacketUtils.send_pkt(self, payload=triggerfetch, ttl=32, flags="A",
                	seq=ack, ack=seq+1,
                	sport=sourcePort, dport=80,ipid=None,
                	dip=None,debug=False)
		new_packet = PacketUtils.get_pkt(self, timeout=5)
		if new_packet is None:
			return "DEAD"
		else:
			while new_packet is not None:
				if isRST(new_packet):
					return "FIREWALL"
				new_packet = PacketUtils.get_pkt(self, timeout=5)
			return "LIVE"




    # Format is
    # ([], [])
    # The first list is the list of IPs that have a hop
    # or none if none
    # The second list is T/F 
    # if there is a RST back for that particular request
    def traceroute(self, target, hops):
	list_of_ips = []
        rst_requests = []
        
	sourcePort = random.randint(2000, 30000)	
        PacketUtils.send_pkt(self, payload=None, ttl=100, flags="S",
        		seq=None, ack=None,
                        sport=sourcePort, dport=80, ipid=None,
                        dip=None, debug=False)

        packet = PacketUtils.get_pkt(self, timeout=10)
        if packet is None:
	    return (None, None)
	seq = packet[TCP].seq
        ack = packet[TCP].ack
	# send syn
	ip = None
        rst = False
        for ttl in range(1, hops):
	    if rst:
                sourcePort = random.randint(2000, 30000)
                PacketUtils.send_pkt(self, payload=None, ttl=100, flags="S",
                        seq=None, ack=None,
                        sport=sourcePort, dport=80, ipid=None,
                        dip=None, debug=False)
                packet = PacketUtils.get_pkt(self, timeout=20)
		if packet is None:
			list_of_ips.append(None)
                	rst_requests.append(False)
                	continue
                seq = packet[TCP].seq
                ack = packet[TCP].ack
            # Send 3 packets to server with modified TTL
            PacketUtils.send_pkt(self, payload=triggerfetch, ttl=ttl, flags="PA", seq=ack,
                                ack=seq+1, sport=sourcePort, dport=80, ipid=None, dip=None, debug=False)
            PacketUtils.send_pkt(self, payload=triggerfetch, ttl=ttl, flags="PA", seq=ack,
                                ack=seq+1, sport=sourcePort, dport=80, ipid=None, dip=None, debug=False)
            PacketUtils.send_pkt(self, payload=triggerfetch, ttl=ttl, flags="PA", seq=ack,
                                ack=seq+1, sport=sourcePort, dport=80, ipid=None, dip=None, debug=False)

            data_packet = PacketUtils.get_pkt(self, timeout=5)
	    if data_packet is None:
	    	list_of_ips.append(None)
                rst_requests.append(False)
		continue
	    ip = None
            rst = False
            while data_packet:
                # Get the packets ip to see if there was a hop
		if isRST(data_packet):	
		        rst = True
                if isICMP(data_packet) and isTimeExceeded(data_packet):
                        ip = data_packet[IP].src
		data_packet = PacketUtils.get_pkt(self, timeout=1)
	    
	    list_of_ips.append(ip)
            rst_requests.append(rst)

        return (list_of_ips, rst_requests)

