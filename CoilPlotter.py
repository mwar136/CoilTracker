import PyDragonfly
from PyDragonfly import CMessage, MT_EXIT, copy_to_msg, copy_from_msg
from dragonfly_utils import respond_to_ping
import Dragonfly_config as rc
from argparse import ArgumentParser
#from configobj import ConfigObj
import numpy as np
import sys
import copy

from ctypes import *
from scipy.io import loadmat
import quaternionarray as qa
import amcmorl_py_tools.vecgeom as vg

import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import axes3d

from PyQt4 import QtCore, QtGui


#QtCore.pyqtRemoveInputHook()
#import ipdb; ipdb.set_trace()

def nan_array(shape, dtype=float):
    a = np.empty(shape, dtype)
    a.fill(np.NAN)
    return a


class MplCanvas(FigureCanvas):
    subscriptions = [MT_EXIT,
                     rc.MT_PING, 
                     rc.MT_HOTSPOT_POSITION]  
    
    def __init__(self, parent=None, width=8, height=10, dpi=80):
        self.parent = parent
        self.paused = False
        self.LiveData = None

        self.figure = Figure(figsize=(width, height), dpi=dpi)
        FigureCanvas.__init__(self, self.figure)

        self.setParent(parent)

        FigureCanvas.setSizePolicy(self,
                                   QtGui.QSizePolicy.Expanding,
                                   QtGui.QSizePolicy.Expanding)
        FigureCanvas.updateGeometry(self)


    def run(self, config_file, mm_ip):
        self.mod = RTMA_Module(0, 0)
        self.mod.ConnectToMMM(mm_ip)
        for sub in self.subscriptions:
            self.mod.Subscribe(sub)
        self.mod.SendModuleReady()
        print "Connected to PolarisDragonfly at", server

        self.init_plot()
        
        #self.set_active_chans()

        timer = QtCore.QTimer(self)
        QtCore.QObject.connect(timer, QtCore.SIGNAL("timeout()"), self.timer_event)
        timer.start(10)

    def init_plot(self):
        self.fig = plt.figure()
        self.ax = fig.gca(projection='3d')
        self.ax.quiver3D([], [], [], [], [], [], pivot='tail', length=vg.norm('length of vector'))
        self.relim()
        self.autoscale()
        self.tight_layout()
        self.draw()

        self.coil_tail = []
        self.coil_head = []
       
    def update_judging_data(self):
        new_head = self.coil_head
        new_tail = self.coil_tail
        temp2 = np.concatenate((self.LiveSpikeData, new_spikes), axis=1)
        self.LiveSpikeData = np.delete(temp2, 0, axis=1)


    def load_config(self):
        self.config = ConfigObj(self.config_file, unrepr=True)

    def reload_config(self):
        self.load_config()
        self.init_legend()


    def timer_event(self):
        done = False
        while not done:
            msg = CMessage()
            rcv = self.mod.ReadMessage(msg, 0)
            if rcv == 1:
                msg_type = msg.GetHeader().msg_type

                if msg_type == rc.MT_HOTSPOT_POSITION: #rc.MT_RAW_SPIKECOUNT: #rc.MT_SPM_SPIKECOUNT:
                    mdf = rc.MDF_HOTSPOT_POSITION()
                    copy_from_msg(mdf, msg)
                    self.coil_tail = np.array(in_mdf.xyz[:])
                    self.coil_head = np.array(in_mdf.ori[:3])
                    self.update_judging_data()
                
                elif msg_type == rc.MT_PING:
                    respond_to_ping(self.mod, msg, 'PolarisDragonfly')

                #elif msg_type == MT_EXIT:
                #    self.exit()
                #    done = True

            else:
                done = True

        self.update_plot()


    def update_plot(self):
        if self.paused == False:
            LiveSpikeData = self.LiveSpikeData
        else:
            LiveSpikeData = self.PausedSpikeData

        self.tight_layout()


        for ch in np.arange(num_chans):
            data = LiveSpikeData[ch*6]
            for u in np.arange(1,6):
                unit = LiveSpikeData[ch*6 + u, :]
                data[unit > 0] = 1
                
            self.spike_data[ch].set_ydata(data * incr * ch)
            self.ax.draw_artist(self.spike_data[ch])

        self.blit(self.ax.bbox)


    def pause(self, pause_state):
        self.paused = pause_state
        self.PausedSpikeData = copy.deepcopy(self.LiveSpikeData)


    def exit(self):
        print "exiting"
        self.parent.exit_app()


    def stop(self):
        print 'disconnecting'
        self.mod.SendSignal(rc.MT_EXIT_ACK)
        self.mod.DisconnectFromMMM()


class MainWindow(QtGui.QMainWindow):
    def __init__(self, config, mm_ip):

        self.paused = False

        QtGui.QMainWindow.__init__(self)

        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle("SpikePlotter")
        self.setGeometry(500,160,960,700)

        main_widget = QtGui.QWidget(self)
        self.mpl = MplCanvas(self)
        self.pb = QtGui.QPushButton("Pause", self)
        #self.rc = QtGui.QPushButton("Reload Config", self)

        vbox = QtGui.QVBoxLayout(main_widget)
        vbox.addWidget(self.mpl)
        hbox = QtGui.QHBoxLayout(main_widget)
        hbox.addWidget(self.pb)
        #hbox.addWidget(self.rc)
        hbox.addStretch()
        vbox.addLayout(hbox)

        main_widget.setFocus()
        self.setCentralWidget(main_widget)

        self.pb.clicked.connect(self.pause_plot)
        #self.rc.clicked.connect(self.reload_config)

        self.mpl.run(config, mm_ip)


    #def reload_config(self):
    #    self.mpl.reload_config()

    def pause_plot(self):
        if self.paused == True:
            self.paused = False
            self.pb.setText('Pause')
        else:
            self.paused = True
            self.pb.setText('Unpause')
        self.mpl.pause(self.paused)

    def exit_app(self):
        self.close()

    def closeEvent(self, ce):
        self.mpl.stop()
        self.close()


if __name__ == "__main__":
    parser = ArgumentParser(description = "Visualizes Spike Data")
    parser.add_argument(type=str, dest='config')
    parser.add_argument(type=str, dest='mm_ip', nargs='?', default='')
    args = parser.parse_args()
    print("Using config file=%s, MM IP=%s" % (args.config, args.mm_ip))

    qApp = QtGui.QApplication(sys.argv)
    frame = MainWindow(args.config, args.mm_ip)
    frame.show()
    sys.exit(qApp.exec_())