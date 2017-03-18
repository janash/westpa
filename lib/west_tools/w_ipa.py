import warnings
#warnings.filterwarnings('ignore', category=DeprecationWarning)
#warnings.filterwarnings('ignore', category=RuntimeWarning)
#warnings.filterwarnings('ignore', category=FutureWarning)
import numpy as np
import h5py

# Must be run with the WEST wrapper.
from westpa import h5io
from westpa.h5io import WESTPAH5File
from westpa.extloader import get_object
import westpa
import os, sys
import w_assign, w_direct, w_reweight
#warnings.filterwarnings('ignore')
import scipy.sparse as sp

from westtools import (WESTSubcommand, WESTParallelTool, WESTDataReader, WESTDSSynthesizer, BinMappingComponent, 
                       ProgressIndicatorComponent, IterRangeSelection, Plotter)

# This is nothing more than a fancy dictionary which has attributes AND keys.
# It's useful for the 'end user experience', and through inheritance, we can ensure a whole
# cascade of dictionaries utilizes this.
class __custom_dataset__(object):
    def __init__(self, raw, key):
        self.__dict__ = {}
        self.raw = raw
        self.name = key
    def __repr__(self):
        return repr(self.__dir__())
    def __getitem__(self, value):
        if value in self.__dict__['raw'].keys():
            return self.__dict__['raw'][value]
        elif value in self.__dict__.keys():
            return self.__dict__[value]
    def __setitem__(self, key, value):
        self.__dict__[key] = value
    def __getattr__(self, value):
        if value in self.__dict__['raw'].keys():
            return self.__dict__['raw'][value]
        elif value in self.__dict__.keys():
            return self.__dict__[value]
    def __setattr__(self, key, value):
        self.__dict__[key] = value
    def __dir__(self):
        dict_keys = self.__dict__.keys()
        remove = ['raw', 'name', '__dict__']
        for i in remove:
            try:
                dict_keys.remove(str(i))
            except:
                pass
        return sorted(set(list(self.raw.keys()) + dict_keys))
    def keys(self):
        print(self.__dir__())
                

class WIPI(WESTParallelTool):
    '''
        Welcome to w_ipa (WESTPA Interactive Python Analysis)!
        From here, you can run traces, look at weights, progress coordinates, etc.
        This is considered a 'stateful' tool; that is, the data you are pulling is always pulled
        from the current analysis scheme and iteration.
        By default, the first analysis scheme in west.cfg is used, and you are set at iteration 1.

        ALL PROPERTIES ARE ACCESSED VIA w or west
        To see the current iteration, try:

            w.iteration
            OR
            west.iteration

        to set it, simply plug in a new value.

            w.iteration = 100

        To change/list the current analysis schemes:

            w.list_schemes
            w.current_scheme = OUTPUT FROM w.list_schemes

        To see the states and bins defined in the current analysis scheme:

            w.states
            w.bin_labels

        All information about the current iteration is available in a dictionary called 'current'.

            w.current.keys():
            walkers, summary, states, seg_id, weights, parents, kinavg, pcoord, bins, populations, and auxdata, if it exists.

        Populations prints the bin and state populations calculated by w_assign; it contains the following attributes:

            states, bins

            which can be called as w.current['populations'].states to return a numpy object.

        If postanalysis has been run, the following information is also available:

            instant_matrix, matrix (the aggregate matrix), kinrw

        Both the kinrw and kinavg key in 'current' have the following attributes:

            expected, error, flux, ferror, raw

            where raw is returned on a basic call.

        kinavg, states, and bins are pulled from the output from w_kinavg and w_assign; they always correspond to
        what is used in the current analysis scheme.  If you change the scheme, those, too, will change.

        You can look at the information for any walker by simply indexing according to that seg_id.

        Information about the previous iteration is available in the past dictionary, which contains the same information.
        It is keyed to use the current iteration's seg_id, such that if you're looking at walker 0 in the current iteration,
        w.past['pcoord'][0] will give you the progress coordinate for the parent of walker 0.  You can look at the actual
        walker seg_id in the previous iteration by
        
            w.past['parents'][0]

        The assign and direct files from the current state are available for raw access.  The postanalysis output
        is also available, should it exist:

            w.assign, w.direct, w_reweight

        In addition, the function w.trace(seg_id) will run a trace over a seg_id in the current iteration and return a dictionary
        containing all pertinent information about that seg_id's history.  It's best to store this, as the trace can be expensive.

        Run help on any function or property for more information!

        Happy analyzing!
                
    '''

    def __init__(self):
        super(WIPI,self).__init__()
        self.data_reader = WESTDataReader()
        self.wm_env.default_work_manager = self.wm_env.default_parallel_work_manager

        self._iter = 1
        self.config_required = True
        self.version = ".99B"
        # Set to matplotlib if you want that.  But why would you?
        # Well, whatever, we'll just set it to that for now.
        self.interface = 'matplotlib'
        global iteration

    def add_args(self, parser):
        self.data_reader.add_args(parser)
        rgroup = parser.add_argument_group('runtime options')
        rgroup.add_argument('--analysis-only', '-ao', dest='analysis_mode', action='store_true',
                             help='''Use this flag to run the analysis and return to the terminal.''')
        rgroup.add_argument('--reanalyze', '-ra', dest='reanalyze', action='store_true',
                             help='''Use this flag to delete the existing files and reanalyze.''')
        rgroup.add_argument('--terminal', '-t', dest='plotting', action='store_true',
                             help='''Plot output in terminal.''')
        # There is almost certainly a better way to handle this, but we'll sort that later.
        rgroup.add_argument('--f', '-f', dest='extra', default='blah',
                             help='''Temporary holding place for when this is called in a Jupyter notebook.''')
        
        parser.set_defaults(compression=True)

    def process_args(self, args):
        self.data_reader.process_args(args)
        with self.data_reader:
            self.niters = self.data_reader.current_iteration - 1
        self.__config = westpa.rc.config
        self.__settings = self.__config['west']['analysis']
        for ischeme, scheme in enumerate(self.__settings['analysis_schemes']):
            if (self.__settings['analysis_schemes'][scheme]['enabled'] == True or self.__settings['analysis_schemes'][scheme]['enabled'] == None):
                self.scheme = scheme
        self.data_args = args
        self.analysis_mode = args.analysis_mode
        self.reanalyze = args.reanalyze
        if args.plotting:
            self.interface = 'text'

    def hash_args(self, args, extra=None):
        '''Create unique hash stamp to determine if arguments/file is different from before.'''
        '''Combine with iteration to know whether or not file needs updating.'''
        # Why are we not loading this functionality into the individual tools?
        # While it may certainly be useful to store arguments (and we may well do that),
        # it's rather complex and nasty to deal with pickling and hashing arguments through
        # the various namespaces.
        # In addition, it's unlikely that the functionality is desired at the individual tool level,
        # since we'll always just rewrite a file when we call the function.
        import hashlib
        import cPickle as pickle
        return hashlib.md5(pickle.dumps([args, extra])).hexdigest()

    def stamp_hash(self, h5file_name, new_hash):
        '''Loads a file, stamps it, and returns the opened file in read only'''
        h5file = h5io.WESTPAH5File(h5file_name, 'r+')
        h5file.attrs['arg_hash'] = new_hash
        h5file.close()
        h5file = h5io.WESTPAH5File(h5file_name, 'r')
        return h5file

    def analysis_structure(self):
        '''
        Run automatically on startup.  Parses through the configuration file, and loads up all the data files from the different 
        analysis schematics.  If they don't exist, it creates them automatically by hooking in to existing analysis routines 
        and going from there.  

        It does this by calling in the make_parser_and_process function for w_{assign,reweight,direct} using a custom built list
        of args.  The user can specify everything in the configuration file that would have been specified on the command line.

        For instance, were one to call w_direct as follows:

            w_direct --evolution cumulative --step-iter 1 --disable-correl

        the west.cfg would look as follows:

        west:
          analysis:
            w_direct:
              evolution: cumulative
              step_iter: 1
              extra: ['disable-correl']

        Alternatively, if one wishes to use the same options for both w_direct and w_reweight, the key 'w_direct' can be replaced
        with 'kinetics'.
        '''
        # Make sure everything exists.
        try:
            os.mkdir(self.__settings['directory'])
        except:
            pass
        # Now, check to see whether they exist, and then load them.
        self.__analysis_schemes__ = {}
        for scheme in self.__settings['analysis_schemes']:
            if self.__settings['analysis_schemes'][scheme]['enabled']:
                if self.work_manager.running == False:
                    self.work_manager.startup()
                path = os.path.join(os.getcwd(), self.__settings['directory'], scheme)
                #if 'postanalysis' in self.__settings['analysis_schemes'][scheme] and 'postanalysis' in self.__settings['postanalysis']:
                # Should clean this up.  But it uses the default global setting if a by-scheme one isn't set.
                if 'postanalysis' in self.__settings:
                    if 'postanalysis' in self.__settings['analysis_schemes'][scheme]:
                        pass
                    else:
                        self.__settings['analysis_schemes'][scheme]['postanalysis'] = self.__settings['postanalysis']
                try:
                    os.mkdir(path)
                except:
                    pass
                self.__analysis_schemes__[scheme] = {}
                try:
                    if self.__settings['analysis_schemes'][scheme]['postanalysis'] == True or self.__settings['postanalysis'] == True:
                        analysis_files = ['assign', 'direct', 'reweight']
                    else:
                        analysis_files = ['assign', 'direct']
                except:
                    analysis_files = ['assign', 'direct']
                    self.__settings['analysis_schemes'][scheme]['postanalysis'] = False
                reanalyze_kinetics = False
                for name in analysis_files:
                    arg_hash = None
                    if self.reanalyze == True:
                        reanalyze_kinetics = True
                        try:
                            os.remove(os.path.join(path, '{}.h5'.format(name)))
                        except:
                            pass
                    else:
                        try:
                            # Try to load the hash.  If we fail to load the hash or the file, we need to reload.
                            #if self.reanalyze == True:
                            #    raise ValueError('Reanalyze set to true.')
                            self.__analysis_schemes__[scheme][name] = h5io.WESTPAH5File(os.path.join(path, '{}.h5'.format(name)), 'r')
                            arg_hash = self.__analysis_schemes__[scheme][name].attrs['arg_hash']
                        except:
                            pass
                            # We shouldn't rely on this.
                            # self.reanalyze = True
                    if True:
                        if name == 'assign':
                            assign = w_assign.WAssign()

                            w_assign_config = { 'output': os.path.join(path, '{}.h5'.format(name))}
                            try:
                                w_assign_config.update(self.__settings['w_assign'])
                            except:
                                pass
                            try:
                                w_assign_config.update(self.__settings['analysis_schemes'][scheme]['w_assign'])
                            except:
                                pass
                            args = []
                            for key,value in w_assign_config.iteritems():
                                if key != 'extra':
                                    args.append(str('--') + str(key).replace('_', '-'))
                                    args.append(str(value))
                            # This is for stuff like disabling correlation analysis, etc.
                            if 'extra' in w_assign_config.keys():
                                for value in w_assign_config['extra']:
                                    args.append(str('--') + str(value).replace('_', '-'))
                            # We're just calling the built in function.
                            # This is a lot cleaner than what we had in before, and far more workable.
                            args.append('--config-from-file')
                            args.append('--scheme-name')
                            args.append('{}'.format(scheme))
                            # Why are we calling this if we're not sure we're remaking the file?
                            # We need to load up the bin mapper and states and see if they're the same.
                            assign.make_parser_and_process(args=args)
                            new_hash = self.hash_args(args=args, extra=[self.niters, assign.binning.mapper, assign.states])
                            # Let's check the hash.  If the hash is the same, we don't need to reload.
                            if arg_hash != new_hash or self.reanalyze == True:
                                # If the hashes are different, or we need to reanalyze, delete the file.
                                try:
                                    os.remove(os.path.join(path, '{}.h5'.format(name)))
                                except:
                                    pass
                                print('Reanalyzing file {}.h5 for scheme {}.'.format(name, scheme))
                                reanalyze_kinetics = True
                                # We want to use the work manager we have here.  Otherwise, just let the tool sort out what it needs, honestly.
                                assign.work_manager = self.work_manager

                                assign.go()
                                assign.data_reader.close()

                                # Stamp w/ hash, then reload as read only.
                                self.__analysis_schemes__[scheme][name] = self.stamp_hash(os.path.join(path, '{}.h5'.format(name)), new_hash)
                            del(assign)

                        # Since these are all contained within one tool, now, we want it to just... load everything.
                        if name == 'direct' or name == 'reweight':
                            assignment_file = self.__analysis_schemes__[scheme]['assign']
                            if name == 'direct':
                                analysis = w_direct.WDirect()
                            if name == 'reweight':
                                analysis = w_reweight.WReweight()
                            
                            analysis_config = { 'assignments': os.path.join(path, '{}.h5'.format('assign')), 'output': os.path.join(path, '{}.h5'.format(name)), 'kinetics': os.path.join(path, '{}.h5'.format(name))}

                            # Pull from general analysis options, then general SPECIFIC options for each analysis,
                            # then general options for that analysis scheme, then specific options for the analysis type in the scheme.

                            try:
                                analysis_config.update(self.__settings['kinetics'])
                            except:
                                pass
                            try:
                                analysis_config.update(self.__settings['w_{}'.format(name)])
                            except:
                                pass
                            try:
                                analysis_config.update(self.__settings['analysis_schemes'][scheme]['kinetics'])
                            except:
                                pass
                            try:
                                analysis_config.update(self.__settings['analysis_schemes'][scheme]['w_{}'.format(name)])
                            except:
                                pass

                            # We're pulling in a default set of arguments, then updating them with arguments from the west.cfg file, if appropriate, after setting the appropriate command
                            # Then, we call the magic function 'make_parser_and_process' with the arguments we've pulled in.
                            # The tool has no real idea it's being called outside of its actual function, and we're good to go.
                            args = ['all']
                            for key,value in analysis_config.iteritems():
                                if key != 'extra':
                                    args.append(str('--') + str(key).replace('_', '-'))
                                    args.append(str(value))
                            # This is for stuff like disabling correlation analysis, etc.
                            if 'extra' in analysis_config.keys():
                                for value in analysis_config['extra']:
                                    args.append(str('--') + str(value).replace('_', '-'))
                            # We want to not display the averages, so...
                            args.append('--disable-averages')
                            new_hash = self.hash_args(args=args, extra=[self.niters])
                            if arg_hash != new_hash or self.reanalyze == True or reanalyze_kinetics == True:
                                try:
                                    os.remove(os.path.join(path, '{}.h5'.format(name)))
                                except:
                                    pass
                                print('Reanalyzing file {}.h5 for scheme {}.'.format(name, scheme))
                                analysis.make_parser_and_process(args=args)
                                # We want to hook into the existing work manager.
                                analysis.work_manager = self.work_manager

                                analysis.go()

                                # Open!
                                self.__analysis_schemes__[scheme][name] = self.stamp_hash(os.path.join(path, '{}.h5'.format(name)), new_hash)
                            del(analysis)

        # Make sure this doesn't get too far out, here.  We need to keep it alive as long as we're actually analyzing things.
        self.work_manager.shutdown()
        print("")
        print("Complete!")

    @property
    def assign(self):
        return self.__analysis_schemes__[self.scheme]['assign']

    @property
    def direct(self):
        """
        The output from w_kinavg.py from the current scheme.
        """
        return self.__analysis_schemes__[self.scheme]['direct']

    @property
    def state_labels(self):
        print("State labels and definitions!")
        for istate, state in enumerate(self.assign['state_labels']):
            print('{}: {}'.format(istate, state))
        print('{}: {}'.format(istate+1, 'Unknown'))

    @property
    def bin_labels(self):
        print("Bin definitions! ")
        for istate, state in enumerate(self.assign['bin_labels']):
            print('{}: {}'.format(istate, state))

    @property
    def west(self):
        return self.data_reader.data_manager.we_h5file

    @property
    def reweight(self):
        if self.__settings['analysis_schemes'][self.scheme]['postanalysis'] == True:
            return self.__analysis_schemes__[self.scheme]['reweight']
        else:
            value = "This sort of analysis has not been enabled."
            current = { 'bin_prob_evolution': value, 'color_prob_evolution': value, 'conditional_flux_evolution': value, 'rate_evolution': value, 'state_labels': value, 'state_prob_evolution': value }
            current.update({ 'bin_populations': value, 'iterations': value })
            return current

    @property
    def scheme(self):
        '''
        Returns and sets what scheme is currently in use.
        To see what schemes are available, run:

            w.list_schemes

        '''
        return self._scheme

    @scheme.setter
    def scheme(self, scheme):
        self._future = None
        self._current = None
        self._past = None
        if scheme in self.__settings['analysis_schemes']:
            pass
        else:
            for ischeme, schemename in enumerate(self.__settings['analysis_schemes']):
                if ischeme == scheme:
                    scheme = schemename
        if self.__settings['analysis_schemes'][scheme]['enabled'] == True or self.__settings['analysis_schemes'][scheme]['enabled'] == None:
            self._scheme = scheme
        else:
            print("Scheme cannot be changed to scheme: {}; it is not enabled!".format(scheme))

    @property
    def list_schemes(self):
        '''
        Lists what schemes are configured in west.cfg file.
        Schemes should be structured as follows, in west.cfg:

        west:
          system:
            analysis:
              directory: analysis
              analysis_schemes:
                scheme.1:
                  enabled: True
                  states:
                    - label: unbound
                      coords: [[7.0]]
                    - label: bound
                      coords: [[2.7]]
                  bins:
                    - type: RectilinearBinMapper
                      boundaries: [[0.0, 2.80, 7, 10000]]
        '''
        print("The following schemes are available:")
        print("")
        for ischeme, scheme in enumerate(self.__settings['analysis_schemes']):
            print('{}. Scheme: {}'.format(ischeme, scheme))
        print("")
        print("Set via name, or via the index listed.")
        print("")
        print("Current scheme: {}".format(self.scheme))

    @property
    def iteration(self):
        '''
        Returns/sets the current iteration.
        '''
        #print("The current iteration is {}".format(self._iter))
        return self._iter

    @iteration.setter
    def iteration(self, value):
        print("Setting iteration to iter {}.".format(value))
        if value <= 0:
            print("Iteration must begin at 1.")
            value = 1
        if value > self.niters:
            print("Cannot go beyond {} iterations!".format(self.niters))
            print("Setting to {}".format(self.niters))
            value = self.niters
        self._iter = value
        self._future = None
        self._current = None
        self._past = None
        return self._iter

    @property
    def walkers(self):
        '''
        The number of walkers active in the current iteration.
        '''
        # Returns number of walkers for iteration X.  Assumes current iteration, but can go with different one.
        return self.current['summary']['n_particles']

    @property
    def aggregate_walkers(self):
        return self.west['summary']['n_particles'][:self.iteration].sum()

    # Returns the raw values, but can also calculate things based on them.
    class KineticsIteration(object):
        def __init__(self, kin_h5file, index, assign, iteration=-1):
            self.__dict__ = {}
            self.h5file = kin_h5file
            # Keys:
            #_2D_h5keys = [ 'rate_evolution', 'conditional_flux_evolution' ]
            _2D_h5keys = [ 'conditional_flux_evolution', 'rate_evolution' ]
            _1D_h5keys = [ 'state_pop_evolution', 'color_prob_evolution' ]
            for key in _2D_h5keys:
                self.__dict__[key] = self.__2D_with_error__(key, index, assign)
            for key in _1D_h5keys:
                self.__dict__[key] = self.__1D_with_error__(key, index, assign)

        def __repr__(self):
            return repr(self.__dir__())
        def __getitem__(self, value):
            if value in self.__dict__.keys():
                return self.__dict__[value]
        def __setitem__(self, key, value):
            self.__dict__[key] = value
        def __getattr__(self, value):
            if value in self.__dict__.keys():
                return self.__dict__[value]
        def __setattr__(self, key, value):
            self.__dict__[key] = value
        def __dir__(self):
            dict_keys = self.__dict__.keys()
            # We don't want to show the plotter class; just the plot function
            remove = [ 'h5file', '__dict__']
            for i in remove:
                try:
                    dict_keys.remove(str(i))
                except:
                    pass
            return sorted(set(dict_keys))
            #return sorted(set(self.__dict__.keys()))
        def keys(self):
            print(self.__dir__())

        # We seriously need to rename this.
        class __custom_dataset__(object):
            # This is just allow it to be indexed via properties.
            # Not a huge thing, but whatever.
            def __init__(self, raw, assign, key):
                self.__dict__ = {}
                self.raw = raw
                self.name = key
                self.assign = assign
                self.nstates = assign.attrs['nstates']
                self.dim = len(raw.shape)
            def __repr__(self):
                return repr(self.__dir__())
            def __getitem__(self, value):
                if value in self.__dict__['raw'].dtype.names:
                    return self.__dict__['raw'][value]
                elif value in self.__dict__.keys():
                    return self.__dict__[value]
            def __setitem__(self, key, value):
                self.__dict__[key] = value
            def __getattr__(self, value):
                if value in self.__dict__['raw'].dtype.names:
                    return self.__dict__['raw'][value]
                elif value in self.__dict__.keys():
                    return self.__dict__[value]
            def __setattr__(self, key, value):
                self.__dict__[key] = value
            def __dir__(self):
                dict_keys = self.__dict__.keys()
                # We don't want to show the plotter class; just the plot function
                remove = ['assign', 'dim', 'nstates', 'plotter', '__dict__']
                for i in remove:
                    try:
                        dict_keys.remove(str(i))
                    except:
                        pass
                return sorted(set(list(self.raw.dtype.names) + dict_keys))
            def keys(self):
                print(self.__dir__())
            def _repr_pretty_(self, p, cycle):
                if self.dim == 1:
                    return self._1D_repr_pretty_(p, cycle)
                if self.dim == 2:
                    return self._2D_repr_pretty_(p, cycle)
            def _1D_repr_pretty_(self, p, cycle):
               # We're just using this as a way to print things in a pretty way.  They can still be indexed appropriately.
               # Stolen shamelessly from westtools/kinetics_tool.py
                maxlabellen = max(map(len,self.assign['state_labels']))
                p.text('')
                p.text('{name} data:\n'.format(name=self.name))
                for istate in xrange(self.nstates):
                    p.text('{:{maxlabellen}s}: mean={:21.15e} CI=({:21.15e}, {:21.15e}) * tau^-1\n'
                        .format(self.assign['state_labels'][istate],
                        self.raw['expected'][istate],
                        self.raw['ci_lbound'][istate],
                        self.raw['ci_ubound'][istate],
                        maxlabellen=maxlabellen))
                p.text('To access data, index via the following names:\n')
                p.text(str(self.__dir__()))
                return " "
            def _2D_repr_pretty_(self, p, cycle):
                # We're just using this as a way to print things in a pretty way.  They can still be indexed appropriately.
                # Stolen shamelessly from westtools/kinetics_tool.py
                maxlabellen = max(map(len,self.assign['state_labels']))
                p.text('')
                p.text('{name} data:\n'.format(name=self.name))
                for istate in xrange(self.nstates):
                    for jstate in xrange(self.nstates):
                        if istate == jstate: continue
                        p.text('{:{maxlabellen}s} -> {:{maxlabellen}s}: mean={:21.15e} CI=({:21.15e}, {:21.15e}) * tau^-1\n'
                            .format(self.assign['state_labels'][istate], self.assign['state_labels'][jstate],
                            self.raw['expected'][istate, jstate],
                            self.raw['ci_lbound'][istate, jstate],
                            self.raw['ci_ubound'][istate, jstate],
                            maxlabellen=maxlabellen))
                p.text('To access data, index via the following names:\n')
                p.text(str(self.__dir__()))
                return " "


        def __2D_with_error__(self, h5key, index, assign):
            # Check the start and stop, calculate the block size, and index appropriately.
            # While we could try and automatically generate this above, it's a little more consistent to try it here.
            # This should show the first block for which the current iteration has contributed data.
            self.step_iter = (self.h5file[h5key]['iter_stop'][0] - self.h5file[h5key]['iter_start'][0])[1,0]
            value = ((index-2) // self.step_iter)
            if value < 0:
                value = 0
            raw = self.h5file[h5key][value, :, :]
            error = (raw['ci_ubound'] - raw['ci_lbound']) / (2*raw['expected'])
            expected = raw['expected']
            raw = self.__custom_dataset__(raw, assign, h5key)
            raw.error = error
            raw.plotter = Plotter(self.h5file, h5key, iteration=value, interface='text')
            raw.plot = raw.plotter.plot
            return raw
        def __1D_with_error__(self, h5key, index, assign):
            self.step_iter = (self.h5file[h5key]['iter_stop'][0] - self.h5file[h5key]['iter_start'][0])[1]
            value = ((index-1) // self.step_iter)
            if value < 0:
                value = 0
            raw = self.h5file[h5key][value, :]
            error = (raw['ci_ubound'] - raw['ci_lbound']) / (2*raw['expected'])
            expected = raw['expected']
            raw = self.__custom_dataset__(raw, assign, h5key)
            raw.error = error
            raw.plotter = Plotter(self.h5file, h5key, iteration=value, interface='text')
            raw.plot = raw.plotter.plot
            return raw

    class __get_data_for_iteration__(object):
        '''
        All interesting data from an iteration (current/past).  Whenever you change the scheme or iteration,
        this dictionary is automatically updated.  For the current iteration, it's keyed to the current seg_id.
        For the past iteration, it's keyed to the seg_id in the CURRENT iteration such that:

            w.current[X] & w.past[X]

        returns information about seg_id X in the current iteration and information on seg_ID X's PARENT in the
        preceding iteration.

        Can be indexed via a seg_id, or like a dictionary with the following keys:

            kinavg, weights, pcoord, auxdata (optional), parents, summary, seg_id, walkers, states, bins

        kinavg, states, and bins refer to the output from w_kinavg and w_assign for this iteration
        and analysis scheme.  They are NOT dynamics bins, but the bins defined in west.cfg.  
        
        Has the following properties:

            .minweight, .maxweight

        which return all properties of the segment that matches those criteria in the selected iteration.

        If you change the analysis scheme, so, too, will the important values.
        '''

        def __init__(self, parent, value, seg_ids = None):
            '''
            Initializes and sets the correct data.
            '''
            # We've classed this so that we can override some of the normal functions and allow indexing via seg_id
            self.__dict__ = {}
            iter_group = parent.data_reader.get_iter_group(value)
            self.parent = parent
            current = {}
            current['iteration'] = value
            if seg_ids == None:
                seg_ids = xrange(0, iter_group['seg_index']['weight'].shape[0])
            # Just make these easier to access.
            current['weights'] = iter_group['seg_index']['weight'][seg_ids]
            current['pcoord'] = iter_group['pcoord'][...][seg_ids, :, :]
            try:
                current['auxdata'] = {}
                for key in iter_group['auxdata'].keys():
                    current['auxdata'][key] = iter_group['auxdata'][key][...][seg_ids, :]
            except:
                pass
            current['parents'] = iter_group['seg_index']['parent_id'][seg_ids]
            current['summary'] = parent.data_reader.data_manager.get_iter_summary(int(value))
            current['seg_id'] = np.array(range(0, iter_group['seg_index'].shape[0]))[seg_ids]
            current['walkers'] = current['summary']['n_particles']
            current['states'] = parent.assign['trajlabels'][value-1, :current['walkers'], :][seg_ids]
            current['bins'] = parent.assign['assignments'][value-1, :current['walkers'], :][seg_ids]
            # Calculates the bin population for this iteration.
            nbins = parent.assign['state_map'].shape[0]
            # We have to take the 'unknown' state into account
            nstates = parent.assign['state_labels'].shape[0] + 1
            # Temporarily disabled while I sort out the fact that we shouldn't be using data from w_assign for state populations.
            #current['plot'] = Plotter(parent.direct, parent.reweight, parent.iteration, parent.assign['bin_labels'], parent.assign['state_labels'], current['populations'].states, current['populations'].bins, parent.interface)
            # Now we'll load up the results of the kinetics analysis.
            current['direct'] = parent.KineticsIteration(parent.direct, value, parent.assign, value)
            evolution_datasets = [ 'rate_evolution', 'conditional_flux_evolution', 'state_pop_evolution', 'color_prob_evolution' ]
            # We want to load these up as... oh, who knows, I suppose?
            try:
                current['reweight'] = parent.KineticsIteration(parent.reweight, value, parent.assign, value)
                # We'll make this not a sparse matrix...
                matrix = parent.reweight['iterations/iter_{:08d}'.format(value)]
                # Assume color.
                current['instant_matrix'] = sp.coo_matrix((matrix['flux'][...], (matrix['rows'][...], matrix['cols'][...])), shape=((nbins-1)*2, (nbins-1)*2)).todense()
                reweighting = True
            except:
              # This analysis hasn't been enabled, so we'll simply return the default error message.
                current['reweight'] = parent.reweight['rate_evolution']
                current['instant_matrix'] = parent.reweight['bin_populations']
                current['matrix'] = parent.reweight['bin_populations']
                reweighting = False
            # Check if the analysis has been enabled.  If yes, make them specify dataset dictionaries.  If not, return the thing.
            if reweighting:
                for key in evolution_datasets:
                    current[key] = __custom_dataset__(raw={ 'direct': current['direct'][key], 'reweight': current['reweight'][key] }, key='a')
            else:
                for key in evolution_datasets:
                    current[key] = __custom_dataset__(raw={ 'direct': current['direct'][key] }, name='direct')

            self.raw = current
        def __repr__(self):
            '''
            Returns the dictionary containing the iteration's values.
            '''
            return repr(self.__dict__['raw'].keys())

        def keys(self):
            '''
            Returns the keys function of the internal dictionary.
            '''
            return self.__dict__['raw'].keys()

        def __setitem__(self, key, value):
            self.__dict__[key] = value
        def __getattr__(self, value):
            if value in self.__dict__['raw'].keys():
                return self.__dict__['raw'][value]
            elif value in self.__dict__.keys():
                return self.__dict__[value]
        def __setattr__(self, key, value):
            self.__dict__[key] = value
        def __dir__(self):
            dict_keys = self.__dict__.keys()
            #remove = ['assign', 'dim', 'nstates']
            #for i in remove:
            #    dict_keys.remove(str(i))
            return sorted(set(list(self.__dict__['raw'].keys()) + dict_keys))

        @property
        def maxweight(self):
            '''
            Returns information about the segment which has the largest weight for this iteration.
            '''
            # Is there a faster or cleaner way to do this?  Ah, maybe.
            walker = np.where(self.raw['weights'] == np.max(self.raw['weights']))[0][0]
            return self.__getitem__(walker)

        @property
        def minweight(self):
            '''
            Returns information about the segment which has the smallest weight for this iteration.
            '''
            walker = np.where(self.raw['weights'] == np.min(self.raw['weights']))[0][0]
            return self.__getitem__(walker)


        def __getitem__(self, value):
            '''
            Responsible for handling whether this is treated like a dictionary of data sets, or an array of walker data.
            '''
            # Check to see if we're indexing via any of the active string types.  We should probably break it down via string or int, instead of 'what exists and what doesn't', but it works for now.
            active_items = ['kinavg', 'statepops', 'weights', 'pcoord', 'auxdata', 'parents', 'summary', 'seg_id', 'walkers', 'states', 'bins', 'populations', 'plot', 'instant_matrix', 'kinrw', 'matrix', 'rwstatepops']
            #if value in active_items:
            if type(value) is str:
                # This should handle everything.  Otherwise...
                try:
                    return self.raw[value]
                except:
                    print('{} is not a valid data structure.'.format(value))
            elif type(value) is int or type(value) is np.int64:
                # Otherwise, we assume they're trying to index for a seg_id.
                if value < self.parent.walkers:
                    current = {}
                    seg_items = ['weights', 'pcoord', 'auxdata', 'parents', 'seg_id', 'states']
                    #for i in seg_items:
                    #    current[i] = self.raw[i]
                    current['pcoord'] = self.raw['pcoord'][value, :, :]
                    current['states'] = self.raw['states'][value, :]
                    current['bins'] = self.raw['bins'][value, :]
                    current['parents'] = self.raw['parents'][value]
                    current['seg_id'] = self.raw['seg_id'][value]
                    current['weights'] = self.raw['weights'][value]
                    try:
                        current['auxdata'] = {}
                        for key in self.raw['auxdata'].keys():
                            current['auxdata'][key] = self.raw['auxdata'][key][value]
                    except:
                        pass
                    current = __custom_dataset__(current, 'Segment {} in Iter {}'.format(value, self.iteration))
                    return current
                else:
                    print('INVALID SEG_ID {}.  SEG_ID should be less than {}.'.format(value, self.parent.walkers))

    @property
    def current(self):
        '''
        The current iteration.  See help for __get_data_for_iteration__
        '''
        if self._current == None:
            self._current = self.__get_data_for_iteration__(value=self.iteration, parent=self)
            return self._current
        else:
            return self._current

    @property
    def past(self):
        '''
        The previous iteration.  See help for __get_data_for_iteration__
        '''
        if self.iteration > 1:
            if self._past == None:
                self._past = self.__get_data_for_iteration__(value=self.iteration - 1, seg_ids=self.current['parents'], parent=self)
                return self._past
            else:
                return self._past
        else:
            print("The current iteration is 1; there is no past.")


    def trace(self, seg_id):
        '''
        Runs a trace on a seg_id within the current iteration, all the way back to the beginning,
        returning a dictionary containing all interesting information:

            seg_id, pcoord, states, bins, weights, iteration, auxdata (optional)

        sorted in chronological order.


        Call with a seg_id.
        '''
        # It should be noted that this is not a fast function, but was designed more as a 'proof of principle' of the generality of this approach.
        # It could, and most certainly should, have its speed increased.
        if seg_id >= self.walkers:
            print("Walker seg_id # {} is beyond the max count of {} walkers.".format(seg_id, self.walkers))
            return 1
        current = { 'seg_id': [seg_id], 'pcoord': [self.current['pcoord'][seg_id]], 'states': [self.current['states'][seg_id]], 'weights': [self.current['weights'][seg_id]], 'iteration': [self.iteration], 'bins': [self.current['bins'][seg_id]] }
        try:
            current['auxdata'] = {}
            for key in self.current['auxdata'].keys():
                current['auxdata'][key] = [self.current['auxdata'][key][seg_id]]
        except:
            pass
        parents = self.current['parents']
        for iter in reversed(range(1, self.iteration)):
            #print(iter)
            iter_data = self.__get_data_for_iteration__(value=iter, seg_ids=parents, parent=self)
            current['pcoord'].append(iter_data['pcoord'][seg_id, :, :])
            current['states'].append(iter_data['states'][seg_id, :])
            current['bins'].append(iter_data['bins'][seg_id, :])
            current['seg_id'].append(iter_data['seg_id'][seg_id])
            current['weights'].append(iter_data['weights'][seg_id])
            try:
                for key in self.current['auxdata'].keys():
                    current['auxdata'][key].append(iter_data['auxdata'][key][seg_id])
            except:
                pass
            current['iteration'].append(iter)
            seg_id = iter_data['seg_id'][seg_id]
            if seg_id < 0:
                # Necessary for steady state simulations.  This means they started in that iteration.
                break
            parents = self.__get_data_for_iteration__(value=iter, parent=self)['parents']
        current['seg_id'] = list(reversed(current['seg_id']))
        current['pcoord'] = np.concatenate(np.array(list(reversed(current['pcoord']))))
        current['states'] = np.concatenate(np.array(list(reversed(current['states']))))
        current['bins'] = np.concatenate(np.array(list(reversed(current['bins']))))
        current['weights'] = list(reversed(current['weights']))
        current['iteration'] = list(reversed(current['iteration']))
        try:
            for key in self.current['auxdata'].keys():
                current['auxdata'][key] = np.concatenate(np.array(list(reversed(current['auxdata'][key]))))
        except:
            pass
        return __custom_dataset__(raw=current, key=seg_id)

    @property
    def future(self, value=None):
        '''
        Similar to current/past, but keyed differently and returns different datasets.
        See help for Future.
        '''
        if self._future == None:
            self._future = self.Future(raw=self.__get_children__(), key=None)
            self._future.iteration = self.iteration+1
        return self._future

    class Future(__custom_dataset__):

        # This isn't a real fancy one.
        def __getitem__(self, value):
            if type(value) is str:
                print(self.__dict__.keys())
                try:
                    return self.__dict__['raw'][value]
                except:
                    print('{} is not a valid data structure.'.format(value))
            elif type(value) is int or type(value) is np.int64:
                # Otherwise, we assume they're trying to index for a seg_id.
                #if value < self.parent.walkers:
                current = {}
                seg_items = ['weights', 'pcoord', 'auxdata', 'parents', 'seg_id', 'states']
                current['pcoord'] = self.__dict__['raw']['pcoord'][value]
                current['states'] = self.__dict__['raw']['states'][value]
                current['bins'] = self.__dict__['raw']['bins'][value]
                current['parents'] = self.__dict__['raw']['parents'][value]
                current['seg_id'] = self.__dict__['raw']['seg_id'][value]
                current['weights'] = self.__dict__['raw']['weights'][value]
                try:
                    current['auxdata'] = {}
                    for key in self.__dict__['raw']['auxdata'].keys():
                        current['auxdata'][key] = self.__dict__['raw']['auxdata'][key][value]
                except:
                    pass
                current = __custom_dataset__(current, 'Segment {} in Iter {}'.format(value, self.iteration))
                return current

    def __get_children__(self):
        '''
        Returns all information about the children of a given walker in the current iteration.
        Used to generate and create the future object, if necessary.
        '''
        
        if self.iteration == self.niters:
            print("Currently at iteration {}, which is the max.  There are no children!".format(self.iteration))
            return 0
        iter_data = self.__get_data_for_iteration__(value=self.iteration+1, parent=self)
        _future = { 'weights': [], 'pcoord': [], 'parents': [], 'summary': iter_data['summary'], 'seg_id': [], 'walkers': iter_data['walkers'], 'states': [], 'bins': [] }
        for seg_id in range(0, self.walkers):
            children = np.where(iter_data['parents'] == seg_id)[0]
            if len(children) == 0:
                error = "No children for seg_id {}.".format(seg_id)
                _future['weights'].append(error)
                _future['pcoord'].append(error)
                _future['parents'].append(error)
                _future['seg_id'].append(error)
                _future['states'].append(error)
                _future['bins'].append(error)
            else:
                # Now, we're gonna put them in the thing.
                value = self.iteration+1 
                _future['weights'].append(iter_data['weights'][children])
                _future['pcoord'].append(iter_data['pcoord'][...][children, :, :])
                try:
                    aux_data = iter_data['auxdata'][...][children, :, :]
                    try:
                        current['aux_data'].append(aux_data)
                    except:
                        current['aux_data'] = aux_data
                except:
                    pass
                _future['parents'].append(iter_data['parents'][children])
                _future['seg_id'].append(iter_data['seg_id'][children])
                _future['states'].append(self.assign['trajlabels'][value-1, children, :])
                _future['bins'].append(self.assign['assignments'][value-1, children, :])
        return _future

    def go(self):
        '''
        Function automatically called by main() when launched via the command line interface.
        Generally, call main, not this function.
        '''
        self.data_reader.open()
        self.analysis_structure()
        # Seems to be consistent with other tools, such as w_assign.  For setting the iterations.
        self.data_reader.open()
        self.niters = self.data_reader.current_iteration - 1
        self.iteration = 1

    @property
    def introduction(self):
        '''
        Just spits out an introduction, in case someone doesn't call help.
        '''
        help_string = '''
        Call as a dictionary item, unless item is a .property; then simply call on the item itself

        w.past, w.current, w.future:
            
            weights, pcoord, seg_id, parents, auxdata, summary, walkers, states, bins, matrix, instant_matrix

                matrix          - aggregate transition matrix.
                instant_matrix  - instant transition matrix (uses current iteration only)
                bins            - bin assignments for walkers from current assignment file
                states          - state assignments for walkers from current assignment file

            kinavg, kinrw - call as is for native dataset, or:

                .expected, .error, .raw, .flux, .ferror
                expected, ci_ubound, ci_lbound, sterr, corrlen

            population.states, population.bin

        w.iteration     - Get/set current iteration
        w.niters        - Maximum number of iterations
        w.scheme        - Get/set current analysis scheme
        w.list_schemes  - Lists all analysis schemes, and current
        w.bin_labels    - pcoord values for bin assignments from current assignment file
        w.state_labels  - state labels for states from current assignment file

        The following give raw access to the h5 files associated with the current scheme

        w.west
        w.assign
        w.direct
        w.reweight

        w.trace()
        '''
        print(help_string)

    @property
    def help(self):
        ''' Just a minor function to call help on itself.  Only in here to really help someone get help.'''
        help(self)

    def _repr_pretty_(self, p, cycle):
        self.introduction
        return " "

    def __dir__(self):
        return_list = ['past', 'current', 'future']
        return_list += ['iteration', 'niters', 'scheme', 'list_schemes', 'bin_labels', 'state_labels', 'west', 'assign', 'direct', 'reweight', 'trace']
        return sorted(set(return_list))



west = WIPI()
w = west
if __name__ == '__main__':
    # We're gonna print some defaults.
    print("")
    print("Welcome to w_ipa (WESTPA Interactive Python Analysis) v. {}!".format(w.version))
    print("Run w.introduction for a more thorough introduction, or w.help to see a list of options.")
    print("Running analysis & loading files.")
    w.main()
    print('Your current scheme, system and iteration are : {}, {}, {}'.format(w.scheme, os.getcwd(), w.iteration))
    if w.analysis_mode == False:
        from IPython import embed, embed_kernel
        from IPython.lib.kernel import find_connection_file
        import IPython
        # We're using this to set magic commands.
        # Mostly, we're using it to allow tab completion of objects stored in dictionaries.
        try:
            # Worked on MacOS.  Probably just an older version.
            c = IPython.Config()
        except:
            # Seems to be necessary on Linux, and likely on newer installs.
            c = IPython.terminal.ipapp.load_default_config()
        c.IPCompleter.greedy = True
        embed(banner1='',
             exit_msg='Leaving w_ipa... goodbye.',
             config=c)
    print("")
