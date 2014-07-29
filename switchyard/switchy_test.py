import sys
import argparse
import os
import signal
import re
import subprocess
import time
import threading
from queue import Queue,Empty
import importlib
import bz2
import hashlib
import pickle
import base64
import fnmatch
import copy
from collections import namedtuple

from switchyard.lib.packet import *
from switchyard.lib.address import *
from switchyard.lib.common import *
from switchyard.lib.testing import *



class FakePyLLNet(LLNetBase):
    '''
    A class that can used for testing code that uses PyLLNet.  Doesn't
    actually do any "real" network interaction; just manufactures
    packets of various sorts to test whether an IP router using this
    class behaves in what appear to be correct ways.
    '''
    def __init__(self, scenario):
        LLNetBase.__init__(self)
        self.devinfo = scenario.interfaces()
        self.scenario = scenario
        self.timestamp = 0.0

    def shutdown(self):
        '''
        For FakePyLLNet, do nothing.
        '''
        pass

    def recv_packet(self, timeout=0.0, timestamp=False):
        '''
        Receive packets from any device on which one is available.
        Blocks until it receives a packet.  Returns None,None,None
        when device(s) are shut down (i.e., on a SIGINT to the process).

        Returns a tuple of device,timestamp,packet, where
            device: network device name on which packet was received
                    as a string
            timestamp: floating point value of time at which packet
                    was received
            packet: POX ethernet packet object
        '''
        # check if we're done with test scenario
        if self.scenario.done():
            raise Shutdown()

        ev = self.scenario.next()
        if ev.match(SwitchyTestEvent.EVENT_INPUT) == SwitchyTestEvent.MATCH_SUCCESS:
            self.scenario.testpass()
            return ev.generate_packet(timestamp, self.timestamp)
        else:
            self.scenario.testfail("recv_packet called, but I was expecting {}".format(str(ev)))

    def send_packet(self, devname, pkt):
        if self.scenario.done():
            raise ScenarioFailure("send_packet was called, but the scenario was finished.")

        ev = self.scenario.next()
        match_results = ev.match(SwitchyTestEvent.EVENT_OUTPUT, device=devname, packet=pkt)
        if match_results == SwitchyTestEvent.MATCH_SUCCESS:
            self.scenario.testpass()
        elif match_results == SwitchyTestEvent.MATCH_FAIL:
            self.scenario.testfail("send_packet was called, but I was expecting {}".format(str(ev)))
        else:
            # Not a pass or fail yet: means that we
            # are expecting more PacketOutputEvent objects before declaring
            # that the expectation matches/passes
            pass
        self.timestamp += 1.0

def run_tests(scenario_names, usercode_entry_point, no_pdb, verbose):
    '''
    Given a list of scenario names, set up fake network object with the
    scenario objects, and invoke the user module.

    (list(str), function, bool, bool) -> None
    '''
    for sname in scenario_names:
        sobj = get_test_scenario_from_file(sname)
        net = FakePyLLNet(sobj)

        log_info("Starting test scenario {}".format(sname))
        exc,value,tb = None,None,None
        message = '''All tests passed!'''
        try:
            usercode_entry_point(net)
        except Shutdown:
            pass
        except SwitchyException:
            exc,value,tb = sys.exc_info()
            message = '''Your code crashed before I could run all the tests.'''
        except ScenarioFailure:
            exc,value,tb = sys.exc_info()
            message = '''Your code didn't crash, but a test failed.'''
        except Exception:
            exc,value,tb = sys.exc_info()
            message = '''Some kind of crash occurred before I could run all the tests.'''

        # there may be a pending SIGALRM for ensuring test completion;
        # turn it off.
        signal.signal(signal.SIGALRM, signal.SIG_IGN)

        sobj.print_summary()

        # if we got an exception, print some contextual information
        # and dump the user into pdb to try to see what happened.
        if exc is not None:
            failurecontext = ''
            if sobj.get_failed_test() is not None:
                failurecontext = '\n'.join([' ' * 4 + s for s in textwrap.wrap(sobj.get_failed_test().description, 60)])
                failurecontext += '\n{}In particular:\n'.format(' ' * 4)
            failurecontext += '\n'.join([' ' * 8 + s for s in textwrap.wrap(str(exc), 60)])

            with red():
                print ('''{}
{}
{}

This is the Switchyard equivalent of the blue screen of death.
Here (repeating what's above) is the failure that occurred:

{}
'''.format('*'*60, message, '*'*60, failurecontext), file=sys.stderr)

            if not verbose:
                message = "You can rerun with the -v flag to include full dumps of packets that may have caused errors. (By default, only relevant packet context may be shown, not the full contents.)"
                print (textwrap.fill(message, 70))

            if no_pdb:
                print (textwrap.fill("You asked not to be put into the Python debugger.  You got it.",70))
            else:
                print ('''
I'm throwing you into the Python debugger (pdb) at the point of failure.
If you don't want pdb, use the --nopdb flag to avoid this fate.

    - Type "help" or "?" to get a list of valid debugger commands.
    - Type "exit" to get out.
    - Type "where" or "bt" to print a full stack trace.
    - You can use any valid Python commands to inspect variables
      for figuring out what happened.

''')
                if tb is not None:
                    pdb.post_mortem(tb)
                else:
                    print ("No exception traceback available")

        else:
            with green():
                print ('{}'.format(message), file=sys.stderr)


def main_test(compile, scenarios, usercode, dryrun, no_pdb, verbose):
    '''
        Entrypoint function for either compiling or running test scenarios.

    (bool, list(str), str, bool, bool, bool) -> None
    '''
    if not scenarios or not len(scenarios):
        log_failure("In test mode, but no scenarios specified.")
        return

    if compile:
        for scenario in scenarios:
            log_info("Compiling scenario {}".format(scenario))
            compile_scenario(scenario)
    else:
        usercode_entry_point = import_user_code(usercode)
        if dryrun:
            log_info("Imported your code successfully.  Exiting dry run.")
            return
        run_tests(scenarios, usercode_entry_point, no_pdb, verbose)

# decorate the "real" debugger entrypoint by
# disabling any SIGALRM invocations -- just ignore
# them if we're going into the debugger
import pdb
def setup_debugger(real_debugger):
    def inner():
        from switchyard.switchyard.switchy import disable_timer
        disable_timer()
        return real_debugger
    return inner()
debugger = setup_debugger(pdb.set_trace)