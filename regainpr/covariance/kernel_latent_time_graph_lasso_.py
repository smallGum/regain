"""Graphical latent variable models selection over time via ADMM.

This adds the possibility to specify a temporal constraint with a kernel
function.
"""
from __future__ import division

import warnings
from functools import partial

import numpy as np
from scipy import linalg
from six.moves import map, range, zip
from sklearn.utils.extmath import squared_norm

from regain.covariance.time_graph_lasso_ import TimeGraphLasso, logl
from regain.norm import l1_od_norm
from regain.prox import prox_logdet, prox_trace_indicator, soft_thresholding
from regain.update_rules import update_rho
from regain.utils import convergence
from regain.validation import check_norm_prox

from regainpr.covariance.kernel_time_graph_lasso_ import objective as obj_ktgl


def objective(
        S, n_samples, R, Z_0, Z_M, W_0, W_M, alpha, tau, kernel_psi,
        kernel_phi, psi, phi):
    """Objective function for latent variable time-varying graphical lasso."""
    obj = obj_ktgl(n_samples, S, R, Z_0, Z_M, alpha, kernel_psi, psi)
    obj += tau * sum(map(partial(np.linalg.norm, ord='nuc'), W_0))

    for m in range(1, W_0.shape[0]):
        # all possible markovians jumps
        W_L, W_R = W_M[m]
        obj += np.sum(
            np.array(list(map(phi, W_R - W_L))) * np.diag(kernel_phi, m))

    return obj


def kernel_latent_time_graph_lasso(
        emp_cov, alpha=0.01, tau=1., rho=1., kernel_psi=None, kernel_phi=None,
        max_iter=100, verbose=False, psi='laplacian', phi='laplacian',
        mode='admm', tol=1e-4, rtol=1e-4, assume_centered=False,
        n_samples=None, return_history=False, return_n_iter=True,
        update_rho_options=None, compute_objective=True):
    r"""Time-varying latent variable graphical lasso solver.

    Solves the following problem via ADMM:
        min sum_{i=1}^T -n_i log_likelihood(K_i-L_i) + alpha ||K_i||_{od,1}
            + tau ||L_i||_*
            + sum_{s>t}^T k_psi(s,t) Psi(K_s - K_t)
            + sum_{s>t}^T k_phi(s,t)(L_s - L_t)

    where S is the empirical covariance of the data
    matrix D (training observations by features).

    Parameters
    ----------
    emp_cov : ndarray, shape (n_features, n_features)
        Empirical covariance of data.
    alpha, tau, beta, eta : float, optional
        Regularisation parameters.
    rho : float, optional
        Augmented Lagrangian parameter.
    max_iter : int, optional
        Maximum number of iterations.
    tol : float, optional
        Absolute tolerance for convergence.
    rtol : float, optional
        Relative tolerance for convergence.
    return_history : bool, optional
        Return the history of computed values.

    Returns
    -------
    K, L : numpy.array, 3-dimensional (T x d x d)
        Solution to the problem for each time t=1...T .
    history : list
        If return_history, then also a structure that contains the
        objective value, the primal and dual residual norms, and tolerances
        for the primal and dual residual norms at each iteration.

    """
    psi, prox_psi, psi_node_penalty = check_norm_prox(psi)
    phi, prox_phi, phi_node_penalty = check_norm_prox(phi)
    n_times, _, n_features = emp_cov.shape

    if kernel_psi is None:
        kernel_psi = np.eye(n_times)
    if kernel_phi is None:
        kernel_phi = np.eye(n_times)

    Z_0 = np.zeros_like(emp_cov)
    W_0 = np.zeros_like(Z_0)
    X_0 = np.zeros_like(Z_0)
    R_old = np.zeros_like(Z_0)

    Z_M = {}
    X_M = {}
    Z_M_old = {}
    W_M = {}
    U_M = {}
    W_M_old = {}
    for m in range(1, n_times):
        Z_L = np.zeros_like(Z_0)[:-m]
        Z_R = np.zeros_like(Z_0)[m:]
        Z_M[m] = (Z_L, Z_R)

        W_L = np.zeros_like(Z_L)
        W_R = np.zeros_like(Z_R)
        W_M[m] = (W_L, W_R)

        X_L = np.zeros_like(Z_L)
        X_R = np.zeros_like(Z_R)
        X_M[m] = (X_L, X_R)

        U_L = np.zeros_like(W_L)
        U_R = np.zeros_like(W_R)
        U_M[m] = (U_L, U_R)

        Z_L_old = np.zeros_like(Z_L)
        Z_R_old = np.zeros_like(Z_R)
        Z_M_old[m] = (Z_L_old, Z_R_old)

        W_L_old = np.zeros_like(W_L)
        W_R_old = np.zeros_like(W_R)
        W_M_old[m] = (W_L_old, W_R_old)

    if n_samples is None:
        n_samples = np.ones(n_times)

    checks = []
    for iteration_ in range(max_iter):
        # update R
        A = Z_0 - W_0 - X_0
        A += A.transpose(0, 2, 1)
        A /= 2.
        A *= -rho / n_samples[:, None, None]
        A += emp_cov
        # A = emp_cov / rho - A

        R = np.array(
            [prox_logdet(a, lamda=ni / rho) for a, ni in zip(A, n_samples)])

        # update Z_0
        A = R + W_0 + X_0
        for m in range(1, n_times):
            A[:-m] += Z_M[m][0] - X_M[m][0]
            A[m:] += Z_M[m][1] - X_M[m][1]

        A /= n_times
        # soft_thresholding_ = partial(soft_thresholding, lamda=alpha / rho)
        # Z_0 = np.array(map(soft_thresholding_, A))
        Z_0 = soft_thresholding(A, lamda=alpha / (rho * n_times))
        
        # update residuals
        X_0 += R - Z_0 + W_0

        # other Zs
        for m in range(1, n_times):
            X_L, X_R = X_M[m]
            A_L = Z_0[:-1] + X_L
            A_R = Z_0[1:] + X_R
            if not psi_node_penalty:
                prox_e = prox_psi(
                    A_R - A_L,
                    lamda=2. * np.diag(kernel_psi, m)[:, None, None] / rho)
                Z_L = .5 * (A_L + A_R - prox_e)
                Z_R = .5 * (A_L + A_R + prox_e)
            else:
                Z_L, Z_R = prox_psi(
                    np.concatenate((A_L, A_R), axis=1),
                    lamda=.5 * np.diag(kernel_psi, m)[:, None, None] / rho,
                    rho=rho, tol=tol, rtol=rtol, max_iter=max_iter)
            Z_M[m] = (Z_L, Z_R)

            # update other residuals
            X_L += Z_0[:-m] - Z_L
            X_R += Z_0[m:] - Z_R

        # update W_0
        A = Z_0 - R - X_0
        for m in range(1, n_times):
            A[:-m] += W_M[m][0] - U_M[m][0]
            A[m:] += W_M[m][1] - U_M[m][1]

        A /= n_times
        A += A.transpose(0, 2, 1)
        A /= 2.

        W_0 = np.array(
            [prox_trace_indicator(a, lamda=tau / (rho * n_times)) for a in A])

        # other Ws
        for m in range(1, n_times):
            U_L, U_R = U_M[m]
            A_L = W_0[:-1] + U_L
            A_R = W_0[1:] + U_R
            if not phi_node_penalty:
                prox_e = prox_phi(A_R - A_L, lamda=2. * np.diag(kernel_phi, m)[:, None, None] / rho)
                W_L = .5 * (A_L + A_R - prox_e)
                W_R = .5 * (A_L + A_R + prox_e)
            else:
                W_L, W_R = prox_phi(
                    np.concatenate((A_L, A_R), axis=1),
                    lamda=.5 * np.diag(kernel_phi, m)[:, None, None] / rho,
                    rho=rho, tol=tol, rtol=rtol, max_iter=max_iter)
            W_M[m] = (W_L, W_R)

            # update other residuals
            U_L += W_0[:-m] - W_L
            U_R += W_0[m:] - W_R

        # diagnostics, reporting, termination checks
        rnorm = np.sqrt(
            squared_norm(R - Z_0 + W_0) + squared_norm(Z_0[:-1] - Z_1) +
            squared_norm(Z_0[1:] - Z_2) + squared_norm(W_0[:-1] - W_1) +
            squared_norm(W_0[1:] - W_2))

        snorm = rho * np.sqrt(
            squared_norm(R - R_old) + squared_norm(Z_1 - Z_1_old) +
            squared_norm(Z_2 - Z_2_old) + squared_norm(W_1 - W_1_old) +
            squared_norm(W_2 - W_2_old))

        obj = objective(emp_cov, n_samples, R, Z_0, Z_1, Z_2, W_0, W_1, W_2,
                        alpha, tau, beta, eta, psi, phi) \
            if compute_objective else np.nan

        check = convergence(
            obj=obj, rnorm=rnorm, snorm=snorm,
            e_pri=np.sqrt(R.size + 4 * Z_1.size) * tol + rtol * max(
                np.sqrt(
                    squared_norm(R) + squared_norm(Z_1) + squared_norm(Z_2) +
                    squared_norm(W_1) + squared_norm(W_2)),
                np.sqrt(
                    squared_norm(Z_0 - W_0) + squared_norm(Z_0[:-1]) +
                    squared_norm(Z_0[1:]) + squared_norm(W_0[:-1]) +
                    squared_norm(W_0[1:]))),
            e_dual=np.sqrt(R.size + 4 * Z_1.size) * tol + rtol * rho * (
                np.sqrt(
                    squared_norm(X_0) + squared_norm(X_1) + squared_norm(X_2) +
                    squared_norm(U_1) + squared_norm(U_2))))

        R_old = R.copy()
        Z_1_old = Z_1.copy()
        Z_2_old = Z_2.copy()
        W_1_old = W_1.copy()
        W_2_old = W_2.copy()

        if verbose:
            print(
                "obj: %.4f, rnorm: %.4f, snorm: %.4f,"
                "eps_pri: %.4f, eps_dual: %.4f" % check[:5])

        checks.append(check)
        if check.rnorm <= check.e_pri and check.snorm <= check.e_dual:
            break

        rho_new = update_rho(
            rho, rnorm, snorm, iteration=iteration_,
            **(update_rho_options or {}))
        # scaled dual variables should be also rescaled
        X_0 *= rho / rho_new
        X_1 *= rho / rho_new
        X_2 *= rho / rho_new
        U_1 *= rho / rho_new
        U_2 *= rho / rho_new
        rho = rho_new
    else:
        warnings.warn("Objective did not converge.")

    covariance_ = np.array([linalg.pinvh(x) for x in Z_0])
    return_list = [Z_0, W_0, covariance_]
    if return_history:
        return_list.append(checks)
    if return_n_iter:
        return_list.append(iteration_)
    return return_list


class LatentTimeGraphLasso(TimeGraphLasso):
    """Sparse inverse covariance estimation with an l1-penalized estimator.

    Parameters
    ----------
    alpha : positive float, default 0.01
        Regularization parameter for precision matrix. The higher alpha,
        the more regularization, the sparser the inverse covariance.

    tau : positive float, default 1
        Regularization parameter for latent variables matrix. The higher tau,
        the more regularization, the lower rank of the latent matrix.

    beta : positive float, default 1
        Regularization parameter to constrain precision matrices in time.
        The higher beta, the more regularization,
        and consecutive precision matrices in time are more similar.

    psi : {'laplacian', 'l1', 'l2', 'linf', 'node'}, default 'laplacian'
        Type of norm to enforce for consecutive precision matrices in time.

    eta : positive float, default 1
        Regularization parameter to constrain latent matrices in time.
        The higher eta, the more regularization,
        and consecutive latent matrices in time are more similar.

    phi : {'laplacian', 'l1', 'l2', 'linf', 'node'}, default 'laplacian'
        Type of norm to enforce for consecutive latent matrices in time.

    rho : positive float, default 1
        Augmented Lagrangian parameter.

    over_relax : positive float, deafult 1
        Over-relaxation parameter (typically between 1.0 and 1.8).

    tol : positive float, default 1e-4
        Absolute tolerance to declare convergence.

    rtol : positive float, default 1e-4
        Relative tolerance to declare convergence.

    max_iter : integer, default 100
        The maximum number of iterations.

    verbose : boolean, default False
        If verbose is True, the objective function, rnorm and snorm are
        printed at each iteration.

    assume_centered : boolean, default False
        If True, data are not centered before computation.
        Useful when working with data whose mean is almost, but not exactly
        zero.
        If False, data are centered before computation.

    time_on_axis : {'first', 'last'}, default 'first'
        If data have time as the last dimension, set this to 'last'.
        Useful to use scikit-learn functions as train_test_split.

    update_rho_options : dict, default None
        Options for the update of rho. See `update_rho` function for details.

    compute_objective : boolean, default True
        Choose if compute the objective function during iterations
        (only useful if `verbose=True`).

    mode : {'admm'}, default 'admm'
        Minimisation algorithm. At the moment, only 'admm' is available,
        so this is ignored.

    Attributes
    ----------
    covariance_ : array-like, shape (n_times, n_features, n_features)
        Estimated covariance matrix

    precision_ : array-like, shape (n_times, n_features, n_features)
        Estimated pseudo inverse matrix.

    latent_ : array-like, shape (n_times, n_features, n_features)
        Estimated latent variable matrix.

    n_iter_ : int
        Number of iterations run.

    """

    def __init__(
            self, alpha=0.01, tau=1., beta=1., eta=1., mode='admm', rho=1.,
            time_on_axis='first', tol=1e-4, rtol=1e-4, psi='laplacian',
            phi='laplacian', max_iter=100, verbose=False,
            assume_centered=False, update_rho_options=None,
            compute_objective=True):
        super(LatentTimeGraphLasso, self).__init__(
            alpha=alpha, beta=beta, mode=mode, rho=rho, tol=tol, rtol=rtol,
            psi=psi, max_iter=max_iter, verbose=verbose,
            time_on_axis=time_on_axis, assume_centered=assume_centered,
            update_rho_options=update_rho_options,
            compute_objective=compute_objective)
        self.tau = tau
        self.eta = eta
        self.phi = phi

    def get_observed_precision(self):
        """Getter for the observed precision matrix.

        Returns
        -------
        precision_ : array-like,
            The precision matrix associated to the current covariance object.
            Note that this is the observed precision matrix.

        """
        return self.precision_ - self.latent_

    def _fit(self, emp_cov, n_samples):
        """Fit the LatentTimeGraphLasso model to X.

        Parameters
        ----------
        emp_cov : ndarray, shape (n_features, n_features)
            Empirical covariance of data.

        """
        self.precision_, self.latent_, self.covariance_, self.n_iter_ = \
            latent_time_graph_lasso(
                emp_cov, n_samples=n_samples,
                alpha=self.alpha, tau=self.tau, rho=self.rho,
                beta=self.beta, eta=self.eta, mode=self.mode,
                tol=self.tol, rtol=self.rtol, psi=self.psi, phi=self.phi,
                max_iter=self.max_iter, verbose=self.verbose,
                return_n_iter=True, return_history=False,
                update_rho_options=self.update_rho_options,
                compute_objective=self.compute_objective)
        return self
