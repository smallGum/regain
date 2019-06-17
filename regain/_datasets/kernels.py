from __future__ import division

import warnings
from functools import partial

import numpy as np
from scipy import signal
from scipy.spatial.distance import squareform
from sklearn.datasets.base import Bunch
from sklearn.utils import deprecated

from regain.utils import (
    ensure_posdef, is_pos_def, is_pos_semidef, positive_definite)

def make_exp_sine_squared(n_dim_obs=5, n_dim_lat=0, T=1, **kwargs):
    from regain.bayesian.gaussian_process_ import sample as samplegp
    from scipy.spatial.distance import squareform
    from sklearn.gaussian_process import kernels

    L, K_HO = make_ell(n_dim_obs, n_dim_lat)

    periodicity = kwargs.get('periodicity', np.pi)
    length_scale = kwargs.get('length_scale', 2)
    epsilon = kwargs.get('epsilon', 0.5)
    sparse = kwargs.get('sparse', True)
    temporal_kernel = kernels.ExpSineSquared(
        periodicity=periodicity, length_scale=length_scale)(
            np.arange(T)[:, None])

    u = samplegp(temporal_kernel, p=n_dim_obs * (n_dim_obs - 1) // 2)[0]
    K, K_obs = [], []
    for uu in u.T:
        theta = squareform(uu)

        if sparse:
            # sparsify
            theta[np.abs(theta) < epsilon] = 0

        theta += np.diag(np.sum(np.abs(theta), axis=1) + 0.01)
        K.append(theta)

        assert (is_pos_def(theta))
        theta_observed = theta - L
        assert (is_pos_def(theta_observed))
        K_obs.append(theta_observed)

    thetas = np.array(K)
    return thetas, np.array(K_obs), np.array([L] * T)


def make_RBF(n_dim_obs=5, n_dim_lat=0, T=1, **kwargs):
    from regain.bayesian.gaussian_process_ import sample as samplegp
    from scipy.spatial.distance import squareform
    from sklearn.gaussian_process import kernels

    length_scale = kwargs.get('length_scale', 1.0)
    length_scale_bounds = kwargs.get('length_scale_bounds', (1e-05, 100000.0))
    epsilon = kwargs.get('epsilon', 0.8)
    sparse = kwargs.get('sparse', True)
    temporal_kernel = kernels.RBF(
        length_scale=length_scale, length_scale_bounds=length_scale_bounds)(
            np.arange(T)[:, None])

    n = n_dim_obs + n_dim_lat
    u = samplegp(temporal_kernel, p=n * (n - 1) // 2)[0]
    K = []
    for i, uu in enumerate(u.T):
        theta = squareform(uu)
        if sparse:
            theta_obs = theta[n_dim_lat:, n_dim_lat:]
            theta_lat = theta[:n_dim_lat, :n_dim_lat]
            theta_OH = theta[n_dim_lat:, :n_dim_lat]

            # sparsify
            theta_obs[np.abs(theta_obs) < epsilon] = 0
            theta_lat[np.abs(theta_lat) < epsilon/3] = 0
            theta_OH[np.abs(theta_OH) < epsilon/3] = 0
            theta[n_dim_lat:, n_dim_lat:] = theta_obs
            theta[:n_dim_lat, :n_dim_lat] = theta_lat
            theta[n_dim_lat:, :n_dim_lat] = theta_OH
            theta[:n_dim_lat, n_dim_lat:] = theta_OH.T
        if i == 0:
            inter_links = theta[n_dim_lat:, :n_dim_lat]
        theta[n_dim_lat:, :n_dim_lat] = inter_links
        theta[:n_dim_lat, n_dim_lat:] = inter_links.T
        theta += np.diag(np.sum(np.abs(theta), axis=1) + 0.01)
        K.append(theta)

        assert (is_pos_def(theta))

    thetas = np.array(K)

    theta_obs = []
    ells = []
    for t in thetas:
        L = theta[n_dim_lat:, :n_dim_lat].dot(
            pinv(t[:n_dim_lat, :n_dim_lat])).dot(
            theta[:n_dim_lat, n_dim_lat:])
        theta_obs.append(t[n_dim_lat:, n_dim_lat:] - L)
        ells.append(L)
    return thetas, theta_obs, ells
