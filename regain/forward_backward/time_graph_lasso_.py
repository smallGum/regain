"""Time graph lasso via forward_backward (for now only in case of l1 norm)."""
from __future__ import division, print_function

import warnings
from functools import partial

import numpy as np
from scipy import linalg
from six.moves import map, range, zip
from sklearn.utils.extmath import squared_norm
from sklearn.covariance import empirical_covariance

from regain.covariance.graph_lasso_ import logl
from regain.covariance.time_graph_lasso_ import TimeGraphLasso
from regain.norm import l1_od_norm, vector_p_norm
from regain.prox import prox_FL
from regain.utils import convergence, positive_definite


def loss(S, K, n_samples=None, vareps=0):
    """Loss function for time-varying graphical lasso."""
    if n_samples is None:
        n_samples = np.ones(S.shape[0])
    loss_ = sum(-ni * logl(emp_cov, precision)
                for emp_cov, precision, ni in zip(S, K, n_samples))
    # loss_ += vareps / 2. * squared_norm(K)
    loss_ += vareps / 2. * _scalar_product(K, K)
    return loss_


def grad_loss(x, emp_cov, n_samples, x_inv=None, vareps=0):
    """Gradient of the loss function for the time-varying graphical lasso."""
    if x_inv is None:
        x_inv = np.array([linalg.pinvh(_) for _ in x])
    grad = emp_cov - x_inv
    grad *= n_samples[:, None, None]

    # add coercitive term
    grad += vareps * x

    return grad


def penalty(precision, alpha, beta, psi):
    """Penalty for time-varying graphical lasso."""
    if isinstance(alpha, np.ndarray):
        obj = sum(a[0][0] * m for a, m in zip(alpha, map(l1_od_norm, precision)))
    else:
        obj = alpha * sum(map(l1_od_norm, precision))
    obj += beta * psi(precision[1:] - precision[:-1])
    return obj


def objective(n_samples, emp_cov, precision, alpha, beta, psi, vareps=0):
    """Objective function for time-varying graphical lasso."""
    obj = loss(emp_cov, precision, n_samples=n_samples, vareps=vareps)
    obj += penalty(precision, alpha, beta, psi)
    return obj


def _J(x, beta, alpha, gamma, lamda, S, n_samples, p=1, x_inv=None, grad=None):
    """Grad + prox + line search for the new point."""
    # if grad is None:
    #     grad = grad_loss(x, S, n_samples, x_inv=x_inv)
    prox = prox_FL(
        x - gamma * grad, beta * gamma, alpha * gamma, p=p, symmetric=True)
    return x + lamda * (prox - x)


def _scalar_product(x, y):
    return (x * y).sum()


def choose_gamma(gamma, x, emp_cov, n_samples, beta, alpha, lamda, grad,
                 delta=1e-4, eps=0.5, max_iter=1000, p=1, x_inv=None,
                 vareps=1e-5):
    """Choose gamma for backtracking.

    References
    ----------
    Salzo S. (2017). https://doi.org/10.1137/16M1073741

    """
    # if grad is None:
    #     grad = grad_loss(x, emp_cov, n_samples, x_inv=x_inv)

    partial_f = partial(loss, n_samples=n_samples, S=emp_cov, vareps=vareps)
    fx = partial_f(K=x)
    for i in range(max_iter):
        prox = prox_FL(
            x - gamma * grad, beta * gamma, alpha * gamma, p=p, symmetric=True)
        y_minus_x = prox - x
        loss_diff = partial_f(K=x + lamda * y_minus_x) - fx

        tolerance = _scalar_product(y_minus_x, grad)
        tolerance += delta / gamma * _scalar_product(y_minus_x, y_minus_x)
        if loss_diff <= lamda * tolerance:
            return gamma
        gamma *= eps
    return gamma


def choose_lamda(lamda, x, emp_cov, n_samples, beta, alpha, gamma, delta=1e-4,
                 eps=0.5, max_iter=1000, criterion='b', p=1, x_inv=None,
                 grad=None, prox=None, min_eigen_x=None,
                 vareps=1e-5):
    """Choose alpha for backtracking.

    References
    ----------
    Salzo S. (2017). https://doi.org/10.1137/16M1073741

    """
    # if x_inv is None:
    #     x_inv = np.array([linalg.pinvh(_) for _ in x])
    # if grad is None:
    #     grad = grad_loss(x, emp_cov, n_samples, x_inv=x_inv)
    # if prox is None:
    #     prox = prox_FL(x - gamma * grad, beta * gamma, alpha * gamma, p=p, symmetric=True)

    partial_f = partial(loss, n_samples=n_samples, S=emp_cov, vareps=vareps)
    fx = partial_f(K=x)

    min_eigen_y = np.min([np.linalg.eigh(z)[0] for z in prox])

    y_minus_x = prox - x
    if criterion == 'b':
        tolerance = _scalar_product(y_minus_x, grad)
        tolerance += delta / gamma * _scalar_product(y_minus_x, y_minus_x)
    elif criterion == 'c':
        psi = partial(vector_p_norm, p=p)
        gx = penalty(x, alpha, beta, psi)
        gy = penalty(prox, alpha, beta, psi)
        objective_x = objective(
            n_samples, emp_cov, x, alpha, beta, psi, vareps=vareps)
        tolerance = (1 - delta) * (gy - gx + _scalar_product(y_minus_x, grad))

    for i in range(max_iter):
        # line-search
        x1 = x + lamda * y_minus_x

        if criterion == 'a':
            iter_diff = x1 - x
            gradx1 = grad_loss(x1, emp_cov, n_samples)
            grad_diff = gradx1 - grad
            norm_grad_diff = np.sqrt(_scalar_product(grad_diff, grad_diff))
            norm_iter_diff = np.sqrt(_scalar_product(iter_diff, iter_diff))
            tolerance = delta * norm_iter_diff / (gamma * lamda)
            if norm_grad_diff <= tolerance:
                break
        elif criterion == 'b':
            loss_diff = partial_f(K=x1) - fx
            if loss_diff <= lamda * tolerance:
                break
        elif criterion == 'c':
            obj_diff = objective(
                n_samples, emp_cov, x1, alpha, beta, psi, vareps=vareps) \
                    - objective_x
            # if positive_definite(x1) and obj_diff <= lamda * tolerance:
            cond = lamda > 0 if min_eigen_y >= 0 else lamda < min_eigen_x / (min_eigen_x - min_eigen_y)
            if cond and obj_diff <= lamda * tolerance:
                break
        else:
            raise ValueError(criterion)
        lamda *= eps
    return lamda, i + 1


def fista_step(Y, Y_diff, t):
    t_next = (1. + np.sqrt(1.0 + 4.0 * t*t)) / 2.
    return Y + ((t - 1.0)/t_next) * Y_diff, t_next


def upper_diag_3d(x):
    """Return the flattened upper diagonal of a 3d matrix."""
    # n_times, n_dim, _ = x.shape
    # upper_idx = np.triu_indices(n_dim, 1)
    # return np.array([xx[upper_idx] for xx in x])
    return np.triu(x, 1)


def time_graph_lasso(
        emp_cov, n_samples, alpha=0.01, beta=1., max_iter=100, verbose=False,
        tol=1e-4, delta=1e-4, gamma=1., lamda=1., eps=0.5, debug=False,
        return_history=False, return_n_iter=True, choose='gamma',
        lamda_criterion='b', time_norm=1, compute_objective=True,
        return_n_linesearch=False, vareps=1e-5):
    """Time-varying graphical lasso solver.

    Solves the following problem via ADMM:
        minimize  trace(S*X) - log det X + lambda*||X||_1

    where S is the empirical covariance of the data
    matrix D (training observations by features).

    Parameters
    ----------
    data_list : list of 2-dimensional matrices.
        Input matrices.
    lamda : float, optional
        Regularisation parameter.
    rho : float, optional
        Augmented Lagrangian parameter.
    alpha : float, optional
        Over-relaxation parameter (typically between 1.0 and 1.8).
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
    X : numpy.array, 2-dimensional
        Solution to the problem.
    history : list
        If return_history, then also a structure that contains the
        objective value, the primal and dual residual norms, and tolerances
        for the primal and dual residual norms at each iteration.

    """
    available_choose = ('gamma', 'lamda', 'fixed', 'both')
    if choose not in available_choose:
        raise ValueError("`choose` parameter must be one of %s." %
                         available_choose)

    n_times, _, n_features = emp_cov.shape
    covariance_ = emp_cov.copy()
    covariance_ *= 0.95

    K = np.empty_like(emp_cov)
    for i, (c, e) in enumerate(zip(covariance_, emp_cov)):
        c.flat[::n_features + 1] = e.flat[::n_features + 1]
        K[i] = linalg.pinvh(c)

    # K = np.array([np.eye(s.shape[0]) for s in emp_cov])
    # Y = K.copy()

    checks = []
    obj_partial = partial(
        objective, n_samples=n_samples, emp_cov=emp_cov,
        alpha=alpha, beta=beta, psi=partial(vector_p_norm, p=time_norm),
        vareps=vareps)

    max_residual = -np.inf
    n_linesearch = 0
    for iteration_ in range(max_iter):
        if not positive_definite(K):
            print("precision is not positive definite.")
            break

        k_previous = K.copy()
        # x_inv = np.array([linalg.pinvh(x) for x in K])
        x_inv = []
        eigens = []
        for x in K:
            es, Q = np.linalg.eigh(x)
            Inv = np.linalg.multi_dot((Q.T, np.diag(1. / es), Q))
            x_inv.append(Inv)
            eigens.append(es)
        x_inv = np.array(x_inv)
        eigens = np.array(eigens)

        grad = grad_loss(K, emp_cov, n_samples, x_inv=x_inv, vareps=vareps)
        if choose in ['gamma', 'both']:
            gamma = choose_gamma(
                gamma / eps if iteration_ > 0 else gamma, K, emp_cov,
                n_samples=n_samples,
                beta=beta, alpha=alpha, lamda=lamda, grad=grad,
                delta=delta, eps=eps, max_iter=200, p=time_norm, x_inv=x_inv,
                vareps=vareps)
        print(gamma)

        x_hat = K - gamma * grad
        y = prox_FL(
            x_hat, beta * gamma, alpha * gamma, p=time_norm, symmetric=True)

        if choose in ['lamda', 'both']:
            lamda, n_ls = choose_lamda(
                min(lamda / eps if iteration_ > 0 else lamda, 1),
                K, emp_cov, n_samples=n_samples,
                beta=beta, alpha=alpha, gamma=gamma, delta=delta, eps=eps,
                criterion=lamda_criterion, max_iter=200, p=time_norm,
                x_inv=x_inv, grad=grad, prox=y,
                min_eigen_x=np.min(eigens),
                vareps=vareps)
            n_linesearch += n_ls
        print (lamda, n_ls)

        K = K + np.maximum(lamda, 0) * (y - K)
        # K, t = fista_step(Y, Y - Y_old, t)

        check = convergence(
            obj=obj_partial(precision=K),
            rnorm=np.linalg.norm(upper_diag_3d(K) - upper_diag_3d(k_previous)),
            snorm=np.linalg.norm(
                obj_partial(precision=K) - obj_partial(precision=k_previous)),
            e_pri=np.sqrt(upper_diag_3d(K).size) * tol + tol * max(
                np.linalg.norm(upper_diag_3d(K)),
                np.linalg.norm(upper_diag_3d(k_previous))),
            e_dual=tol, precision=K.copy())

        if verbose and iteration_ % (50 if verbose < 2 else 1) == 0:
            print("obj: %.4f, rnorm: %.7f, snorm: %.4f,"
                  "eps_pri: %.4f, eps_dual: %.4f" % check[:5])

        if return_history:
            checks.append(check)

        if np.isnan(check.rnorm) or np.isnan(check.snorm):
            warnings.warn("precision is not positive definite.")

        # use this convergence criterion
        subgrad = (x_hat - K) / gamma
        if 0:
            grad = grad_loss(K, emp_cov, n_samples, vareps=vareps)
            res_norm = np.linalg.norm(grad + subgrad)

            if iteration_ == 0:
                normalizer = res_norm + 1e-6
            max_residual = max(np.linalg.norm(grad),
                               np.linalg.norm(subgrad)) + 1e-6
        else:
            res_norm = np.linalg.norm(K - k_previous) / gamma
            max_residual = max(max_residual, res_norm)
            normalizer = max(np.linalg.norm(grad),
                             np.linalg.norm(subgrad)) + 1e-6

        r_rel = res_norm / max_residual
        r_norm = res_norm / normalizer

        if not debug and (r_rel <= tol or r_norm <= tol) and iteration_ > 0: # or (
                # check.rnorm <= check.e_pri and iteration_ > 0):
            break
            # pass
        # if check.rnorm <= check.e_pri and iteration_ > 0:
        #     # and check.snorm <= check.e_dual:
        #     break
    else:
        warnings.warn("Objective did not converge.")

    # for i in range(K.shape[0]):
    #     covariance_[i] = linalg.pinvh(K[i])

    return_list = [K, covariance_]
    if return_history:
        return_list.append(checks)
    if return_n_iter:
        return_list.append(iteration_ + 1)
    if return_n_linesearch:
        return_list.append(n_linesearch)
    return return_list


class TimeGraphLassoForwardBackward(TimeGraphLasso):
    """Sparse inverse covariance estimation with an l1-penalized estimator.

    Parameters
    ----------
    alpha : positive float, default 0.01
        Regularization parameter for precision matrix. The higher alpha,
        the more regularization, the sparser the inverse covariance.

    beta : positive float, default 1
        Regularization parameter to constrain precision matrices in time.
        The higher beta, the more regularization,
        and consecutive precision matrices in time are more similar.

    psi : {'laplacian', 'l1', 'l2', 'linf', 'node'}, default 'laplacian'
        Type of norm to enforce for consecutive precision matrices in time.

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

    n_iter_ : int
        Number of iterations run.

    """

    def __init__(self, alpha=0.01, beta=1., time_on_axis='first', tol=1e-4,
                 max_iter=100, verbose=False, assume_centered=False,
                 compute_objective=True, eps=0.5, choose='gamma', lamda=1,
                 delta=1e-4, gamma=1., lamda_criterion='b', time_norm=1,
                 return_history=False, debug=False,
                 return_n_linesearch=False,
                 vareps=1e-5):
        super(TimeGraphLassoForwardBackward, self).__init__(
            alpha=alpha, tol=tol, max_iter=max_iter,
            verbose=verbose, assume_centered=assume_centered,
            compute_objective=compute_objective, beta=beta,
            time_on_axis=time_on_axis)
        self.delta = delta
        self.gamma = gamma
        self.lamda_criterion = lamda_criterion
        self.time_norm = time_norm
        self.eps = eps
        self.choose = choose
        self.lamda = lamda
        self.return_history = return_history
        self.debug = debug
        self.return_n_linesearch = return_n_linesearch
        self.vareps = vareps

    def _fit(self, emp_cov, n_samples):
        """Fit the TimeGraphLasso model to X.

        Parameters
        ----------
        emp_cov : ndarray, shape (n_time, n_features, n_features)
            Empirical covariance of data.

        """
        if self.alpha == 'max':
            # use sklearn alpha max
            self.alpha = self.alpha_max(emp_cov, is_covariance=True)

        out = time_graph_lasso(
            emp_cov, n_samples=n_samples, alpha=self.alpha, beta=self.beta,
            tol=self.tol, max_iter=self.max_iter, verbose=self.verbose,
            return_n_iter=True, return_history=self.return_history,
            compute_objective=self.compute_objective,
            time_norm=self.time_norm, lamda_criterion=self.lamda_criterion,
            gamma=self.gamma, delta=self.delta, eps=self.eps,
            choose=self.choose, lamda=self.lamda, debug=self.debug,
            return_n_linesearch=self.return_n_linesearch,
            vareps=self.vareps)

        if self.return_history:
            if self.return_n_linesearch:
                self.precision_, self.covariance_, self.history_, self.n_iter_, self.n_linesearch_ = out
            else:
                self.precision_, self.covariance_, self.history_, self.n_iter_ = out
        else:
            if self.return_n_linesearch:
                self.precision_, self.covariance_, self.n_iter_, self.n_linesearch_ = out
            else:
                self.precision_, self.covariance_, self.n_iter_ = out
        return self

    def alpha_max(self, X, is_covariance=False):
        """Compute the alpha_max for the problem at hand, based on sklearn."""
        from sklearn.covariance.graph_lasso_ import alpha_max
        if is_covariance:
            emp_cov = X
        else:
            emp_cov = np.array([empirical_covariance(
                x, assume_centered=self.assume_centered) for x in X])
        return max(alpha_max(e) for e in emp_cov)


def get_lipschitz(data):
    """Get the Lipschitz constant for a specific loss function.

    Only square loss implemented.

    Parameters
    ----------
    data : (n, d) float ndarray
        data matrix
    loss : string
        the selected loss function in {'square', 'logit'}
    Returns
    ----------
    L : float
        the Lipschitz constant
    """
    n, p = data.shape

    if p > n:
        tmp = np.dot(data, data.T)
    else:
        tmp = np.dot(data.T, data)
    return np.linalg.norm(tmp, 2)
