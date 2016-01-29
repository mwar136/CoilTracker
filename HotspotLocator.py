import sys
import os
import logging
import time
import numpy as np
from argparse import ArgumentParser
from IPython.config.loader import PyFileConfigLoader


import PyDragonfly
from PyDragonfly import CMessage, MT_EXIT, copy_to_msg, copy_from_msg
from dragonfly_utils import respond_to_ping

import Dragonfly_config as rc
import quaternionarray as qa
import amcmorl_py_tools.vecgeom as vg


'''
Calibrates the TMS coil and calculates the location of the hotspot in space
Requires 2 seconds to calibrate coil

Order of operations:
1. Loads config file
2. Begins logging
3. Setup dragonfly server
4. User begins calibration with enter- any other response is not accepted
5. Data storage array is created- needs information to be added
6. Run begins and starts message processing
7. Sample alignment is checked- If okay vector is calculated
8. Calibration becomes true so hotspot is now calculated in space using:

Ni = Calibration plate position at calibration time
Ti = Coil marker position at calibration time

Xi = Ni - Ti

Qi = Coil marker orientation at calibration time
Qk = Coil marker orientation at Tc+N
Qr = Rotation from calibration to Tc+N

Qr = Qk * Qi'

Tk = Position of coil at marker
Xk = Position of hotspot at Tc+N

Xk = (Qr*Xi)*Qr' + Tk
'''

class HotspotLocator (object):
    
    plate_vector
    
    def __init__(self, config_file, mm_ip):
        self.load_config(config_file)
        self.load_logging()
        self.setup_dragonfly(mm_ip)
        self.get_frequency()
        self.run()
        
    def load_config(self, config_file):
        cfg = PyFileConfigLoader(config_file)
        cfg.load_config()
        self.config = cfg.config
        
        # special casing for SAMPLE_GENERATED
        if (self.config.trigger == 'SAMPLE_GENERATED'):
            self.config.trigger_msg = rc.MT_SAMPLE_GENERATED
            self.config.trigger_mdf = rc.MDF_SAMPLE_GENERATED
        else:
            self.config.trigger_msg = \
                eval('rc.MT_' + self.config.trigger)
            self.config.trigger_mdf = \
                eval('rc.MDF_' + self.config.trigger)
        print "Triggering with", self.config.trigger
        print "PolarisDragonfly: loading config"
        
        #self.ntools = len(self.config.tool_list)
        self.plate = self.config.tools.index('CB609')
        self.marker = self.config.tools.index('CT315')
         
    def load_logging(self):
        log_file = os.path.normpath(os.path.join(self.config.config_dir, 'coil_calibration.log'))
        print "log file: " + log_file
        logging.basicConfig(filename=log_file, level=logging.DEBUG)
        logging.info(' ')
        logging.info(' ')
        logging.debug("**** STARTING UP ****")
        logging.info("  %s  " % time.asctime())
        logging.info("*********************")
            
    def setup_dragonfly(self, mm_ip):
        self.mod = PyDragonfly.Dragonfly_Module(0, 0)
        self.mod.ConnectToMMM(mm_ip)
        self.mod.Subscribe(MT_EXIT)
        self.mod.Subscribe(rc.MT_PING)
        self.mod.Subscribe(rc.MT_POLARIS_POSITION)
        
        self.mod.SendModuleReady()
        print "PolarisDragonfly: connected to dragonfly"
    
    def get_frequency(self):
        # loop over receiving messages until we get a POLARIS_POSITION message
        while True:
            msg = CMessage()
            rcv = self.mod.ReadMessage(msg, 0.001)
            if rcv == 1:
                msg_type = msg.GetHeader().msg_type
                dest_mod_id = msg.GetHeader().dest_mod_id
                if  msg_type == MT_EXIT:
                    if (dest_mod_id == 0) or (dest_mod_id == self.mod.GetModuleID()):
                        print 'Received MT_EXIT, disconnecting...'
                        self.mod.SendSignal(rc.MT_EXIT_ACK)
                        self.mod.DisconnectFromMMM()
                        break;
                elif msg_type == rc.MT_PING:
                    respond_to_ping(self.mod, msg, 'PolarisDragonfly')
                else:
                    msg_type = msg.GetHeader().msg_type
                    if msg_type == rc.MT_POLARIS_POSITION:
                        # handling input message
                        mdf = rc.MDF_POLARIS_POSITION()
                        copy_from_msg(mdf, msg)
                        self.fsamp = 1/mdf.sample_header.DeltaTime
                        if self.fsamp != 0:
                            break
                        
        self.user_start_calibrate()               
                     
        # (handle EXITS and PINGS appropriately)
        # return 1 / DeltaTime from first POLARIS_POSITION msg
        
        
    
    def user_start_calibrate(self):
        # get a POLARIS_POSITION message, read sample_header.DeltaTime to get
        # message frequency
        while True:
            x = raw_input("Press enter to calibrate...")
            if not x:
                break
            print '.......'
        sys.stdout.write('starting in:')
        sys.stdout.write('5\n')
        sys.stdout.flush()
        time.sleep(1)
        sys.stdout.write('4\n')
        sys.stdout.flush()
        time.sleep(1)
        sys.stdout.write('3\n')
        sys.stdout.flush()
        time.sleep(1)
        sys.stdout.write('2\n')
        sys.stdout.flush()
        time.sleep(1)
        sys.stdout.write('1\n')
        sys.stdout.flush()
        time.sleep(1)
        sys.stdout.write('Calibrating...')
        self.create_storage()
    
    def create_storage (self):
        self.store_plate_pos = np.empty([5 * self.fsamp, 3])
        self.store_plate_ori = np.empty([5 * self.fsamp, 4])
        self.store_coil_pos = np.empty([5 * self.fsamp, 3])
        self.store_coil_ori = np.empty([5 * self.fsamp, 4])
        self.store_plate = 0
        self.store_coil = 0
        self.calibrated = False
        
    def run(self):
        while True:
            msg = CMessage()
            rcv = self.mod.ReadMessage(msg, 0.001)
            if rcv == 1:
                msg_type = msg.GetHeader().msg_type
                dest_mod_id = msg.GetHeader().dest_mod_id
                if  msg_type == MT_EXIT:
                    if (dest_mod_id == 0) or (dest_mod_id == self.mod.GetModuleID()):
                        print 'Received MT_EXIT, disconnecting...'
                        self.mod.SendSignal(rc.MT_EXIT_ACK)
                        self.mod.DisconnectFromMMM()
                        break;
                elif msg_type == rc.MT_PING:
                    respond_to_ping(self.mod, msg, 'PolarisDragonfly')
                else:
                    self.process_message(msg)
    
    def process_message(self, in_msg):

        msg_type = in_msg.GetHeader().msg_type
        if msg_type == rc.MT_POLARIS_POSITION:
            # handling input message
            in_mdf = rc.MDF_POLARIS_POSITION()
            copy_from_msg(in_mdf, in_msg)
            positions = np.array(in_mdf.xyz[:])
            orientations = self.shuffle_q(np.array(in_mdf.ori[:]))
            
            #np.testing.assert_array_equal(positions[:,0], orientations[:,0], err_msg='Samples are not aligned')

            if self.calibrated:
                if in_mdf.tool_id == (self.marker + 1): 
                    # calculating output
                    self.Qk = qa.norm(orientations) #need to find a way to discriminate the tools files in the messages???
                    Qr = qa.mult(self.Qk, qa.inv(self.Qi)).flatten()
                    Tk = positions
                    hotspot_position = (qa.rotate(Qr, self.Xi) + Tk).flatten()
                    hotspot_vector_head = qa.rotate(Qr, plate_vector)
                    if np.any(np.isnan(hotspot_position)) == True:
                        print "x",
                        #print '         *****nan present, check coil is within frame!*****'
                   
                    #creating output message
                    out_mdf = rc.MDF_HOTSPOT_POSITION()
                    out_mdf.xyz[:] = hotspot_position
                    out_mdf.ori[:3] = hotspot_vector_head # Qk - coil active orientation
                    out_mdf.sample_header = in_mdf.sample_header
                    msg = CMessage(rc.MT_HOTSPOT_POSITION)
                    copy_to_msg(out_mdf, msg)
                    self.mod.SendMessage(msg)
                    sys.stdout.write("o")
                                    
            else:
                if np.any(np.isnan(positions)) == True:
                    raise Exception, 'nan present'
                if np.any(np.isnan(orientations)) == True:
                    raise Exception, 'nan present'
                if (
                    (self.store_plate >= self.store_plate_pos.shape[0]) & 
                    (self.store_plate >= self.store_plate_ori.shape[0]) & 
                    (self.store_coil >= self.store_coil_pos.shape[0]) & 
                    (self.store_coil >= self.store_coil_ori.shape[0])
                    ):
                    self.calibrating = False
                    self.make_calibration_vector()
                elif in_mdf.tool_id == (self.marker + 1): 
                    self.store_coil_pos[self.store_coil, :] = positions
                    self.store_coil_ori[self.store_coil, :] = orientations
                    self.store_coil += 1
                elif in_mdf.tool_id == (self.plate + 1):
                    self.store_plate_pos[self.store_plate, :] = positions
                    self.store_plate_ori[self.store_plate, :] = orientations
                    self.store_plate += 1
                
            
    def make_calibration_vector(self):
        plate_ori = qa.norm(self.store_plate_ori.mean(axis=0))
        Ni        = self.store_plate_pos.mean(axis=0)
        self.Qi   = qa.norm(self.store_coil_ori.mean(axis=0))
        Ti        = self.store_coil_pos.mean(axis=0)
        msg_str_pos = "%.5e, " * 3
        msg_str_ori = "%.5e, " * 4
        sys.stdout.write('Plate orientation:    ')
        sys.stdout.write(msg_str_ori % (plate_ori[0], plate_ori[1], plate_ori[2],\
                                    plate_ori[3]) + "\n")
        sys.stdout.write('Plate position:       ')
        sys.stdout.write(msg_str_pos % (Ni[0], Ni[1], Ni[2]) + "\n")
        sys.stdout.write('Coil orientation:     ')
        sys.stdout.write(msg_str_ori % (self.Qi[0], self.Qi[1], self.Qi[2], self.Qi[3]) + "\n")
        sys.stdout.write('Coil position:        ')
        sys.stdout.write(msg_str_pos % (Ti[0], Ti[1], Ti[2]) + "\n")
        self.Xi = Ni - Ti
        sys.stdout.write('Vector:        ')
        sys.stdout.write(msg_str_pos % (self.Xi[0], self.Xi[1], self.Xi[2]) + "\n")
        sys.stdout.write("********** Calibration complete! ***********\n")
        sys.stdout.flush()
        self.calibrated = True
    
    def shuffle_q(self, q):
        return np.roll(q, -1, axis=0)

if __name__ == "__main__":
    parser = ArgumentParser(description = 'Interface with Polaris hardware' \
        ' and emit HOTSPOT_POSITION messages')
    parser.add_argument(type=str, dest='config')
    parser.add_argument(type=str, dest='mm_ip', nargs='?', default='')
    args = parser.parse_args()
    print("Using config file=%s, MM IP=%s" % (args.config, args.mm_ip))
    pdf = HotspotLocator(args.config, args.mm_ip)
    print "Finishing up"