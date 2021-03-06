
import numpy as np
from mpi4py import MPI
import time
import pathlib

from dedalus import public as de
from dedalus.extras import flow_tools
import parameters as param
import forcing

import logging
logger = logging.getLogger(__name__)


# Bases and domain
x_basis = de.Fourier('x', param.N, interval=(0, param.L), dealias=3/2)
y_basis = de.Fourier('y', param.N, interval=(0, param.L), dealias=3/2)
domain = de.Domain([x_basis, y_basis], grid_dtype=np.float64, mesh=param.mesh)

# Stochastic forcing
dkx = dky = 2 * np.pi / param.L
draw = forcing.incomp_shell_2d(param.kf, param.kfw, dkx, dky, param.seed)
Fx = domain.new_field()
Fy = domain.new_field()
kx = domain.elements(0)
ky = domain.elements(1)

# Problem
problem = de.IVP(domain, variables=['p','ux','uy'])
problem.parameters['L'] = param.L
problem.parameters['ν'] = param.ν
problem.parameters['α'] = param.α
problem.parameters['Fx'] = Fx
problem.parameters['Fy'] = Fy
problem.substitutions['ω'] = "dx(uy) - dy(ux)"
problem.substitutions['e'] = "(ux*ux + uy*uy) / 2"
problem.substitutions['z'] = "ω*ω / 2"
problem.substitutions['Lap(a)'] = "dx(dx(a)) + dy(dy(a))"
problem.substitutions['Adv(a)'] = "ux*dx(a) + uy*dy(a)"
problem.substitutions['mean(a)'] = "integ(a) / L**2"
problem.add_equation("dt(ux) - ν*Lap(ux) + α*ux + dx(p) = -Adv(ux) + Fx")
problem.add_equation("dt(uy) - ν*Lap(uy) + α*uy + dy(p) = -Adv(uy) + Fy")
problem.add_equation("dx(ux) + dy(uy) = 0", condition="(nx != 0) or (ny != 0)")
problem.add_equation("p = 0", condition="(nx == 0) and (ny == 0)")

# Build solver
solver = problem.build_solver(param.ts)
solver.stop_sim_time = param.stop_sim_time
solver.stop_wall_time = param.stop_wall_time
solver.stop_iteration = param.stop_iteration
logger.info('Solver built')

# Initial conditions
ux = solver.state['ux']
uy = solver.state['uy']
if pathlib.Path('restart.h5').exists():
    solver.load_state('restart.h5', -1)

# Analysis
snapshots = solver.evaluator.add_file_handler('snapshots', iter=param.snapshots_iter, max_writes=10, mode='overwrite')
snapshots.add_system(solver.state)
snapshots.add_task("Fx")
snapshots.add_task("Fy")
snapshots.add_task("ω")
snapshots.add_task("e")
snapshots.add_task("z")
scalars = solver.evaluator.add_file_handler('scalars', iter=param.scalars_iter, max_writes=100, mode='overwrite')
scalars.add_task("mean(e)", name='E')
scalars.add_task("mean(z)", name='Z')
scalars.add_task("mean(-α*2*e)", name='εα')
scalars.add_task("mean(-α*2*z)", name='ηα')
scalars.add_task("mean(ν*(ux*Lap(ux) + uy*Lap(uy)))", name='εν')
scalars.add_task("mean(ν*ω*Lap(ω))", name='ην')

# Flow properties
flow = flow_tools.GlobalFlowProperty(solver, cadence=10)
flow.add_property("mean(e)", name='E')

# Main loop
dt = param.dt
try:
    logger.info('Starting loop')
    start_time = time.time()
    while solver.ok:
        # Change forcing
        Fx['c'], Fy['c'] = draw(kx, ky)
        # Project onto grid space and scale
        Fx['g'] *= (param.ε / dt)**0.5
        Fy['g'] *= (param.ε / dt)**0.5
        solver.step(dt)
        if (solver.iteration-1) % 10 == 0:
            logger.info('Iteration: %i, Time: %e, dt: %e' %(solver.iteration, solver.sim_time, dt))
            logger.info('Mean KE/M = %f' %flow.max('E'))
except:
    logger.error('Exception raised, triggering end of main loop.')
    raise
finally:
    end_time = time.time()
    logger.info('Iterations: %i' %solver.iteration)
    logger.info('Sim end time: %f' %solver.sim_time)
    logger.info('Run time: %.2f sec' %(end_time-start_time))
    logger.info('Run time: %f cpu-hr' %((end_time-start_time)/60/60*domain.dist.comm_cart.size))
