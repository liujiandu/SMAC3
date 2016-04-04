'''
Created on Dec 15, 2015

@author: Aaron Klein
'''
import sys
import unittest

import numpy as np
from ConfigSpace import ConfigurationSpace, Configuration
from ConfigSpace.util import impute_inactive_values

from smac.smbo.smbo import SMBO
from smac.scenario.scenario import Scenario
from smac.smbo.acquisition import EI
from smac.smbo.local_search import LocalSearch
from smac.utils import test_helpers

if sys.version_info[0] == 2:
    import mock
else:
    from unittest import mock


class ConfigurationMock(object):
    def __init__(self, value=None):
        self.value = value

    def get_array(self):
        return [self.value]


class TestSMBO(unittest.TestCase):

    def setUp(self):
        self.scenario = Scenario({'cs': test_helpers.get_branin_config_space()})
        
    def branin(self, x):
        y = (x[:, 1] - (5.1 / (4 * np.pi ** 2)) * x[:, 0] ** 2 + 5 * x[:, 0] / np.pi - 6) ** 2
        y += 10 * (1 - 1 / (8 * np.pi)) * np.cos(x[:, 0]) + 10

        return y[:, np.newaxis]        

    def test_rng(self):
        smbo = SMBO(self.scenario, None)
        self.assertIsInstance(smbo.rng, np.random.RandomState)
        smbo = SMBO(self.scenario, 1)
        rng = np.random.RandomState(1)
        self.assertIsInstance(smbo.rng, np.random.RandomState)
        smbo = SMBO(self.scenario, rng)
        self.assertIs(smbo.rng, rng)
        # ML: I don't understand the following line and it throws an error
        self.assertRaisesRegexp(TypeError,
                                "Unknown type <(class|type) 'str'> for argument "
                                'rng. Only accepts None, int or '
                                'np.random.RandomState',
                                SMBO, self.scenario, 'BLA')

    def test_choose_next(self):
        seed = 42
        smbo = SMBO(self.scenario, seed)
        X = self.scenario.cs.sample_configuration().get_array()[None, :]

        Y = self.branin(X)
        x = smbo.choose_next(X, Y)[0].get_array()
        assert x.shape == (2,)

    def test_choose_next_2(self):
        def side_effect(X, derivative):
            return [[np.sum(X)]]

        smbo = SMBO(self.scenario, 1)
        smbo.model = mock.MagicMock()
        smbo.acquisition_func.compute = mock.MagicMock()
        smbo.acquisition_func.compute.side_effect = side_effect
        # local search would call the underlying local search maximizer,
        # which would have to be mocked out. Replacing the method by random
        # search is way easier!
        smbo._get_next_by_local_search = smbo._get_next_by_random_search

        X = smbo.rng.rand(10, 2)
        Y = smbo.rng.rand(10, 1)

        x = smbo.choose_next(X, Y)

        self.assertEqual(smbo.model.train.call_count, 1)
        # Will be called once for each data point by random search
        self.assertEqual(smbo.acquisition_func.compute.call_count, 1000)
        self.assertEqual(len(x), 2020)
        num_random_search = 0
        for i in range(0, 2020, 2):
            self.assertIsInstance(x[i], Configuration)
            if x[i].origin == 'Random Search':
                num_random_search += 1
        # Since we replace local search with random search, we have to count
        # the occurences of random seacrh instead
        self.assertEqual(num_random_search, 10)
        for i in range(1, 2020, 2):
            self.assertIsInstance(x[i], Configuration)
            self.assertEqual(x[i].origin, 'Random Search')

    @mock.patch('ConfigSpace.util.impute_inactive_values')
    @mock.patch.object(EI, '__call__')
    @mock.patch.object(ConfigurationSpace, 'sample_configuration')
    def test_get_next_by_random_search_sorted(self,
                                              patch_sample,
                                              patch_ei,
                                              patch_impute):
        values = (10, 1, 9, 2, 8, 3, 7, 4, 6, 5)
        patch_sample.return_value = [ConfigurationMock(i) for i in values]
        patch_ei.return_value = np.array(values, dtype=float)
        patch_impute.side_effect = lambda x: x
        smbo = SMBO(self.scenario, 1)
        rval = smbo._get_next_by_random_search(10, True)
        for i in range(10):
            self.assertIsInstance(rval[i][1], ConfigurationMock)
            self.assertEqual(rval[i][1].value, 10 - i)
            self.assertEqual(rval[i][0], 10 - i)
            self.assertEqual(rval[i][1].origin, 'Random Search (sorted)')

        # Check that config.get_array works as desired and imputation is used
        #  in between
        np.testing.assert_allclose(patch_ei.call_args[0][0],
                                   np.array(values, dtype=float)
                                   .reshape((-1, 1)))

    @mock.patch.object(ConfigurationSpace, 'sample_configuration')
    def test_get_next_by_random_search(self, patch):
        def side_effect(size):
            return [ConfigurationMock()] * size
        patch.side_effect = side_effect
        smbo = SMBO(self.scenario, 1)
        rval = smbo._get_next_by_random_search(10, False)
        for i in range(10):
            self.assertIsInstance(rval[i][1], ConfigurationMock)
            self.assertEqual(rval[i][1].origin, 'Random Search')
            self.assertEqual(rval[i][0], 0)

    @mock.patch.object(LocalSearch, 'maximize')
    def test_get_next_by_local_search(self, patch):
        # Without known incumbent
        class SideEffect(object):
            def __init__(self):
                self.call_number = 0

            def __call__(self, *args, **kwargs):
                rval = 9 - self.call_number
                self.call_number += 1
                return (ConfigurationMock(rval), [[rval]])

        patch.side_effect = SideEffect()
        smbo = SMBO(self.scenario, 1)
        rval = smbo._get_next_by_local_search(num_points=9)
        self.assertEqual(len(rval), 9)
        self.assertEqual(patch.call_count, 9)
        for i in range(9):
            self.assertIsInstance(rval[i][1], ConfigurationMock)
            self.assertEqual(rval[i][1].value, 9 - i)
            self.assertEqual(rval[i][0], 9 - i)
            self.assertEqual(rval[i][1].origin, 'Local Search')

        # With known incumbent
        patch.side_effect = SideEffect()
        smbo.incumbent = 'Incumbent'
        rval = smbo._get_next_by_local_search(num_points=10)
        self.assertEqual(len(rval), 10)
        self.assertEqual(patch.call_count, 19)
        # Only the first local search in each iteration starts from the
        # incumbent
        self.assertEqual(patch.call_args_list[9][0][0], 'Incumbent')
        for i in range(10):
            self.assertEqual(rval[i][1].origin, 'Local Search')


if __name__ == "__main__":
    unittest.main()
