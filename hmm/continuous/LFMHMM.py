__author__ = 'diego'

from hmm._BaseHMM import _BaseHMM
from hmm.lfm2kernel.lfm2 import lfm2
from hmm.lfm2kernel import SecondOrderLFMKernel
from scipy import stats
import numpy as np


class LFMHMMError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return repr(self.value)


class LFMHMM(_BaseHMM):

    def __init__(self, n, A, pi, number_outputs, start_t, end_t,
                 locations_per_segment, damper, spring, lengthscales,
                 noise_var, precision=np.double, verbose=False):
        assert n > 0
        assert locations_per_segment > 0
        assert A.shape == (n, n)
        assert (pi.shape == (n, 1)) or (pi.shape == (n, ))
        assert number_outputs > 0
        assert len(damper) == len(spring) == len(lengthscales) == n
        assert all([len(x) == number_outputs for x in damper])
        assert all([len(x) == number_outputs for x in spring])
        _BaseHMM.__init__(self, n, None, precision, verbose)
        self.n = n  # number of hidden states
        self.A = A  # transition matrix
        self.pi = pi  # initial state PMF
        self.number_outputs = number_outputs
        self.start_t = start_t
        self.end_t = end_t
        self.sample_locations = np.linspace(start_t, end_t,
                                            locations_per_segment)
        self.locations_per_segment = locations_per_segment
        self.spring_cons = spring
        self.damper_cons = damper
        self.lengthscales = lengthscales
        self.noise_var = noise_var
        self.memo_covs = {}
        self.lfms = np.zeros(n, dtype='object')
        # ======
        # TODO: I think this part of the code should be in lfm2.py
        idx = np.zeros(shape=(0, 1), dtype=np.int8)
        time_length = len(self.sample_locations)
        stacked_time = np.zeros(shape=(0, 1))
        for d in xrange(number_outputs):
            idx = np.vstack((idx, d * np.ones((time_length, 1), dtype=np.int8)))
            stacked_time = np.vstack((stacked_time,
                                      self.sample_locations.reshape(-1,1)))
        # ======
        # TODO: make the number of latent forces a parameter.
        # and the same thing with the sensitivities. For now all them are 1.
        self.number_latent_f = 1
        self.sensi = np.ones((number_outputs, self.number_latent_f))
        for i in xrange(n):
            params = np.concatenate((np.log(spring[i]), np.log(damper[i]),
                                     np.hstack(self.sensi),
                                     np.log(np.array([lengthscales[i]])),
                                     np.log(np.array([noise_var]))), axis=0)
            self.lfms[i] = lfm2(1, number_outputs, params)
            self.lfms[i].set_inputs(stacked_time, idx)


    def reset(self,init_type='uniform'):
        '''
        If required, initalize the model parameters according the selected policy
        '''
        if init_type == 'uniform':
            self.pi = np.ones( (self.n), dtype=self.precision) *(1.0/self.n)
            self.A = np.ones( (self.n,self.n), dtype=self.precision)*(1.0/self.n)
            # TODO: allow the emission estimation, i.e. reestimateB
        else:
            raise LFMHMMError("reset init_type not supported.")

    def generate_observations(self, segments, hidden_s=None):
        output = np.zeros((segments, self.locations_per_segment),
                          dtype=self.precision)
        initial_state = np.nonzero(np.random.multinomial(1, self.pi))[0][0]
        hidden_states = [initial_state]
        for x in xrange(1, segments):
            hidden_states.append(np.nonzero(np.random.multinomial(
                1, self.A[hidden_states[-1]]))[0][0])
        if hidden_s:
            hidden_states = hidden_s
        for i in xrange(len(hidden_states)):
            state = hidden_states[i]
            cov = self.get_cov_function(state)
            realization = np.random.multivariate_normal(
                mean=np.zeros(cov.shape[0]), cov=cov)
            output[i, :] = realization
        print "Hidden States", hidden_states
        return output, hidden_states

    def generate_continuous_observations(self, segments):
        # Notice the difference in the number of locations.
        output = np.zeros((segments, self.locations_per_segment - 1),
                          dtype=self.precision)
        initial_state = np.nonzero(np.random.multinomial(1, self.pi))[0][0]
        hidden_states = [initial_state]
        for x in xrange(1, segments):
            hidden_states.append(np.nonzero(np.random.multinomial(
                1, self.A[hidden_states[-1]]))[0][0])
        last_observation_value = 1.0
        for i in xrange(len(hidden_states)):
            state = hidden_states[i]
            cov = self.get_cov_function(state)
            # Conditioning the first value to be equal to the last observation
            # value.
            A = cov[0, 0].item()
            C = cov[0, 1:].reshape((1, -1))
            B = cov[1:, 1:]
            mean_cond = C * (last_observation_value/A)
            cov_cond = B - (1./A) * np.dot(C.T, C)
            realization = np.random.multivariate_normal(
                mean=mean_cond.flatten(), cov=cov_cond)
            output[i, :] = realization
            last_observation_value = realization[-1]
        print "Hidden States", hidden_states
        return output, hidden_states

    def get_cov_function(self, hidden_state, cache=True):
        if cache and (hidden_state in self.memo_covs):
            return self.memo_covs[hidden_state]
        assert hidden_state < self.n
        cov = self.lfms[hidden_state].Kyy()
        self.memo_covs[hidden_state] = cov
        return cov

    def get_cov_function_explicit(self, hidden_state, t, tp):
        B = np.asarray(self.spring_cons[hidden_state])
        C = np.asarray(self.damper_cons[hidden_state])
        l = self.lengthscales[hidden_state]
        # Notice that the noise variance doesn't appear here.
        # The noise variance only affects Ktt.
        cov = SecondOrderLFMKernel.K_pred(B, C, l, t.reshape((-1, 1)),
                                          tp.reshape((-1, 1)))
        return cov

    def predict(self, t_step, hidden_state, obs):
        # TODO: there is a bad smell here. obs vs set observations.
        if self.verbose and \
                (np.any(t_step < self.start_t) or np.any(t_step > self.end_t)):
            print "WARNING:prediction step.Time step out of the sampling region"
        if hidden_state < 0 or hidden_state >= self.n:
            raise LFMHMMError("ERROR: Invalid hidden state.")
        obs = obs.reshape((-1, 1))
        Ktt = self.get_cov_function(hidden_state)
        ktstar = self.get_cov_function_explicit(
            hidden_state, self.sample_locations, np.asarray(t_step))
        Kstarstar = self.get_cov_function_explicit(
            hidden_state, np.asarray(t_step),  np.asarray(t_step))
        mean_pred = np.dot(ktstar.T, np.linalg.solve(Ktt, obs))
        cov_pred = Kstarstar - np.dot(ktstar.T, np.linalg.solve(Ktt, ktstar))
        return mean_pred, cov_pred

    def _mapB(self):
        '''
        Required implementation for _mapB. Refer to _BaseHMM for more details.
        '''
        # Erasing the covariance cache
        self.memo_covs = {}
        if not self.observations:
            raise LFMHMMError("The training sequences haven't been set.")

        numbers_of_sequences = len(self.observations)

        self.B_maps = np.zeros((numbers_of_sequences, self.n), dtype=object)

        for j in xrange(numbers_of_sequences):
            for i in xrange(self.n):
                self.B_maps[j][i] = np.zeros(len(self.observations[j]),
                                             dtype=self.precision)

        # strange behavior found between numpy and stats. See below.

        for j in xrange(numbers_of_sequences):
            number_of_segments = len(self.observations[j])
            for i in xrange(self.n):
                cov = self.get_cov_function(i)
                for t in xrange(number_of_segments):
                    self.B_maps[j][i][t] = stats.multivariate_normal.pdf(
                        self.observations[j][t], np.zeros(cov.shape[0]),
                        cov + self.noise_var * np.eye(cov.shape[0]),
                        True)  # Allowing singularity in cov. This is weird.










