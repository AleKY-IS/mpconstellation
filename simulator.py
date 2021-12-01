import numpy as np
from scipy import integrate
from datetime import datetime
from constants import *
from control import *
from satellite import Satellite

class Simulator:
    def __init__(self, sats=[], controller=Controller()):
        """
        Arguments:
            sats: list of Satellite objects to use for simulation
            controller = Controller object to use for simulation
        """
        self.sim_data = {}  # Data produced by simulator runs
        self.sats = sats
        self.controller = controller

    def run(self, tf=10):
        """
        Arguments:
            tf: rough number of orbits
        Runs a simulation for all satellites.
        Returns an dictionary with satellite IDs as keys and 3 x T arrays of x, y, z coordinates as values.
        """
        pos_dict = {}
        for sat in self.sats:
            u = self.controller.get_u_func()
            curr_pos = self.get_trajectory_ODE(sat, tf, u)
            pos_dict[sat.id] = curr_pos
        self.sim_data = pos_dict
        return self.sim_data

    @staticmethod
    def get_atmo_density(r, r0):
        """
        Arguments:
            r: current normalized position vector
            r0: initial position norm
        Returns: 
            Atmospheric density at given altitude in kg/m^3
        Calculates atmospheric density based on current altitude and is 
        only accurate between 480-520km because of linearization. 
        Based on tabulated Harris-Priester Atmospheric Density Model found
        on page 91 of Satellite Orbits by Gill and Montenbruck
        """
        altitude = np.linalg.norm(r * r0) - R_EARTH
        # return 8E26 * altitude**-6.828 # power model for 400-600km but too slow
        # return -1E-17 * altitude**6E-12 # linear model for 480-520km - also slows down solver slightly 
        return 9.983E-13 # fixed density for 500km


    @staticmethod
    def satellite_dynamics(tau, y, u, tf, constants):
        """
        Arguments:
            tau: normalized time, values from 0 to 1
            y: state vector: [position, velocity, mass] - 7 x 1
            u: thrust function, u = u(tau). This allows for open-loop control only.
            tf: final time used for normalization
            constants: dict, containing keys MU, R_E, J2, S, G0, ISP
        Returns:
            difference to update state vector
        Dynamics function of the form y_dot = f(tau, y, params)
        """
        # References: (2.36 - 2.38, 2.95 - 2.97)
        # Get position, velocity
        r = y[0:3]
        v = y[3:6]
        # Get mass and thrust
        m = y[6]
        thrust = y[7:10]
        r_z = r[2]
        r_norm = np.linalg.norm(r)
        # Position ODE
        y_dot = np.zeros((7,))
        y_dot[0:3] = v # r_dot = v
        # Velocity ODE
        # Accel from gravity
        a_g = -constants['MU']/(r_norm)**3 * r
        # Accel from J2
        A = np.array([ [5*(r_z/r_norm)**2 - 1,0,0], [0,5*(r_z/r_norm)**2 - 1,0], [0,0,5*(r_z/r_norm)**2 - 3]])
        a_j2 = 1.5*constants['J2']*constants['MU']*constants['R_E']**2/np.linalg.norm(r)**5 * np.dot(A, r)
        # Accel from thrust; ignore thrust value
        # TODO(jx) fix how control inputs are processed?
        a_u = u(tau) / (m)
        # Accel from atmospheric drag
        a_d = -1/2 * C_D * constants['S'] * (1 / m) * (Simulator.get_atmo_density(r, constants['R0']) / constants['RHO']) * np.linalg.norm(v) * v
        # TODO(jx): implement accel from solar wind
        y_dot[3:6] = a_g + a_j2 + a_u + a_d
        # Mass ODE
        y_dot[6] = -np.linalg.norm(thrust)/(constants['G0']*constants['ISP'])
        return tf*y_dot

    def get_trajectory_ODE(self, sat, tf, u):
        """
        Arguments:
            sat: Satellite object
            ts: timestep in seconds
            tf: (roughly) number of orbits, i.e. tf = 1 is 1 orbit.
            u: u(tau) takes in a normalized time tau and outputs a 3x1 thrust vector. Used for open-loop control only.
        Returns:
            position: 3 x n array of x, y, z coordinates
        Get trajectory with ODE45, normalized dynamics.
        This function simulates the trajectory of a single satellite using the
        current state as the initial value.
        Designer units (pg. 20)
        """
        r0  = np.linalg.norm(sat.position)
        s0 = 2*np.pi*np.sqrt(r0**3/MU_EARTH)
        v0 = r0/s0
        a0 = r0/s0**2
        m0 = sat.mass
        T0 = m0*r0/s0**2
        mu0 = r0**3/s0**2

        # Normalized state vector (pg. 21)
        y0 = np.concatenate([sat.position/r0, sat.velocity/v0, np.array([sat.mass/m0])])

        # Normalize system parameters (pg. 21)
        const = {'MU': MU_EARTH/mu0, 'R_E': R_EARTH/r0, 'J2': J2, 'S':SA/r0**2, 'G0':G0/a0, 'ISP':ISP/s0, 'R0': r0, 'RHO': m0/r0**3}

        # Solve IVP:
        resolution = (100*tf) + 1 # Generally, higher resolution for more orbits are needed
        times = np.linspace(0, 1, resolution)
        sol = integrate.solve_ivp(Simulator.satellite_dynamics, [0, tf], y0, args=(u, tf, const), t_eval=times, max_step=0.001)
        r = sol.y[0:3,:] # Extract positon vector
        pos = r*r0 # Re-dimensionalize position [m]
        return pos

    # Get trajectory with the forward euler method
    def get_trajectory(self, sat, ts, tf):
        """
        Arguments:
            sat: Satellite object
            ts: timestep in seconds
            tf: final time in seconds
        Returns:
            position: 3 x n array of x, y, z coordinates
        """
        n = int(tf / ts)
        position = np.zeros(shape=(n, 3))
        init = sat.position
        for i in range(n):
            self.update_satellite_state(sat, ts)
            position[i, :] = sat.position
            if np.linalg.norm(position[i, :]) < R_EARTH:
                # If magnitude of position vector for any point is less than the earth's
                # radius, that means the satellite has crashed into earth
                print("Crashed!")
                print(position[i, :])
        final = sat.position
        print(f'Error with ts={ts}')
        print(np.linalg.norm(final) - np.linalg.norm(init))
        return position

    # Forward Euler time-step method
    @staticmethod
    def update_satellite_state(sat, time_step):
        # Update Position
        position_next = sat.velocity * time_step + sat.position

        # Update Velocity
        accel = -(MU_EARTH / np.linalg.norm(sat.position) ** 3) * sat.position + sat.thrust / sat.mass
        velocity_next = accel * time_step + sat.velocity

        # Update Mass
        mass_dot = np.linalg.norm(sat.thrust) / G0 * ISP
        mass_next = mass_dot * time_step + sat.mass

        # Update Satellite object
        sat.position = position_next
        sat.velocity = velocity_next
        sat.mass = mass_next

    def save_to_csv(self, suffix=""):
        """
        Export trajectory to CSV for visualization in MATLAB
        """
        date = datetime.today().strftime('%Y-%m-%d-%H-%M-%S')
        for sat in self.sats:
            np.savetxt(f"trajectory_{date}_{sat.id}{suffix}.csv", self.sim_data[sat.id].T, delimiter=",")
