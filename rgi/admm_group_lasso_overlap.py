from __future__ import division
import numpy as np


def D_function(d, groups):
    D = np.zeros(d)
    for dimension in range(d):
        # find groups which contain this dimension
        for g, group in enumerate(groups):
            if dimension in group:
                D[dimension] += 1
    return D


def group_lasso_overlap(A, b, lamda=1.0, groups=None, rho=1.0,
                        max_iter=100, tol=1e-4, verbose=False, rtol=1e-2):
    n, d = A.shape

    x = [np.zeros(len(g)) for g in groups]  # local variables
    z = np.zeros(d)
    y = [np.zeros(len(g)) for g in groups]

    D = np.diag(D_function(d, groups))
    Atb = A.T.dot(b)
    inv = np.linalg.inv(A.T.dot(A) + rho * D)
    hist = []
    count = 0
    for k in range(max_iter):
        # x update
        for i, g in enumerate(groups):
            x[i] = soft_thresholding(x[i] - y[i] / rho, lamda / rho)

        # z update
        zold = z
        x_consensus = P_star_x_bar_function(x, d, groups)
        y_consensus = P_star_x_bar_function(y, d, groups)
        z = inv.dot(Atb + D.dot(y_consensus + rho * x_consensus))

        for i, g in enumerate(groups):
            y[i] += rho * (x[i] - z[g])

        # diagnostics, reporting, termination checks
        history = (
            objective(A, b, lamda, x, z),  # objective
            np.linalg.norm(x_consensus - z),  # rnorm
            np.linalg.norm(-rho * (z - zold)),  # snorm

            np.sqrt(d) * tol + rtol * max(np.linalg.norm(x_consensus),
                                          np.linalg.norm(-z)),  # eps primal
            np.sqrt(d) * tol + rtol * np.linalg.norm(rho * y_consensus)
            # eps dual
        )

        if verbose:
            print("obj: %.4f, rnorm: %.4f, snorm: %.4f,"
                  "eps_pri: %.4f, eps_dual: %.4f" % history)

        hist.append(history)
        if history[1] < history[3] and history[2] < history[4]:
            if count > 10:
                break
            else:
                count += 1
        else:
            count = 0

    return z, hist



def P_star_x_bar_function(x, d, groups):
    P_star_x_bar = np.zeros(d)
    for dim in range(d):
        ss = 0
        count = 0
        for g, group in enumerate(groups):
            if dim in group:
                idx = np.argwhere(np.array(group) == dim)[0]
                ss += x[g][idx]
                count += 1
        if count > 0:
            ss /= count
        P_star_x_bar[dim] = ss
    return P_star_x_bar


def overlapping_group_lasso(A, b, lamda=1.0, groups=None, rho=1.0, alpha=1.0,
                        max_iter=100, tol=1e-4):
    # % solves the following problem via ADMM:
    # %   minimize 1/2*|| Ax - b ||_2^2 + \lambda sum(norm(x_i))
    # %
    # % The input p is a K-element vector giving the block sizes n_i, so that x_i
    # % is in R^{n_i}.
    # % The solution is returned in the vector x.
    # %
    # % history is a structure that contains the objective value, the primal and
    # % dual residual norms, and the tolerances for the primal and dual residual
    # % norms at each iteration.
    # % rho is the augmented Lagrangian parameter.
    # % alpha is the over-relaxation parameter (typical values for alpha are
    # % between 1.0 and 1.8).
    rtol = 1e-2

    n, d = A.shape
    N = len(groups)

    x = [np.zeros(len(g)) for g in groups]  # local variables
    z = np.zeros(d)
    y = np.zeros(d)



    hist = []
    AtA = A.T.dot(A)
    Atb = A.T.dot(b)
    inverse = np.linalg.inv(N * AtA + rho * np.eye(d))
    for k in range(max_iter):
        # % x-update (to be done in parallel)
        P_star_xk_bar = P_star_x_bar_function(x)
        for i, g in enumerate(groups):
            # x update; update each local x
            x[i] = prox(x[i] + (z - P_star_xk_bar - y / rho)[g], lamda / rho)

        P_star_xk1_bar = P_star_x_bar_function(x)
        z = inverse.dot(Atb + rho * P_star_xk1_bar + y)

        # y-update
        y += rho * (P_star_xk1_bar - z)

        # # % compute the dual residual norm square
        # s = 0
        # q = 0
        # zsold = zs
        # zs = z.reshape(-1,1).dot(np.ones((1, N)))
        # zs += Aixi - Axbar.reshape(-1,1).dot(np.ones((1, N)))
        # for i in range(N):
        #     # % dual residual norm square
        #     s = s + np.linalg.norm(-rho * Ats[i].dot((zs[:,i] - zsold[:,i])))**2
        #     # % dual residual epsilon
        #     q = q + np.linalg.norm(rho*Ats[i].dot(u))**2;
        #
        # # % diagnostics, reporting, termination checks
        # history = []
        # history.append(objective(A, b, lamda, N, x, z))
        # history.append(np.sqrt(N)*np.linalg.norm(z - Axbar))
        # history.append(np.sqrt(s))
        #
        # history.append(np.sqrt(n)*ABSTOL + rtol*max(np.linalg.norm(Aixi,'fro'), np.linalg.norm(-zs, 'fro')))
        # history.append(np.sqrt(n)*ABSTOL + rtol*np.sqrt(q))
        #
        # hist.append(history)
        # if history[1] < history[3] and history[2] < history[4]:
        #     break

    return P_star_x_bar_function(x), x


def fi(xi):
    return np.linalg.norm(xi)


def soft_thresholding(z, lamda):
    norm = np.linalg.norm(z)
    return z - lamda * z / norm if norm > lamda else np.zeros(z.size)


def prox(x, kappa):
    return np.maximum(0, x - kappa) - np.maximum(0, -x - kappa)


def objective(A, b, alpha, x, z):
    return .5 * np.sum((A.dot(z) - b) ** 2) + alpha * np.sum(
        [np.linalg.norm(x_i) for x_i in x])
