# Import packages

from xml.sax.handler import property_declaration_handler
import scipy.signal as ssig
import lalsimulation as lalsim 
import numpy as np
import jax.numpy as jnp
import jax

# from ripple.waveforms.IMRPhenomD import gen_IMRPhenomD_polar
from ripple import ms_to_Mc_eta
from ripple.waveforms.IMRPhenomD import gen_IMRPhenomD_polar
from jaxgw.PE.detector_preset import * 
from jaxgw.PE.heterodyneLikelihood import make_heterodyne_likelihood
from jaxgw.PE.detector_projection import make_detector_response

from flowMC.nfmodel.realNVP import RealNVP
from flowMC.sampler.MALA import make_mala_sampler
from flowMC.sampler.Sampler import Sampler
from flowMC.utils.PRNG_keys import initialize_rng_keys
from flowMC.nfmodel.utils import *


psd_func_dict = {
    'H1': lalsim.SimNoisePSDaLIGOZeroDetHighPower,
    'L1': lalsim.SimNoisePSDaLIGOZeroDetHighPower,
    'V1': lalsim.SimNoisePSDAdvVirgo,
}
ifos = list(psd_func_dict.keys())

# define center of time array
tgps_geo = 1126259462.423

# define sampling rate and duration
fsamp = 2048
duration = 4

delta_t = 1/fsamp
tlen = int(round(duration / delta_t))

freqs = np.fft.rfftfreq(tlen, delta_t)
delta_f = freqs[1] - freqs[0]

# we will want to pad low frequencies; the function below applies a
# prescription to do so smoothly, but this is not really needed: you
# could just set all values below `fmin` to a constant.
fmin = 30
def pad_low_freqs(f, psd_ref):
    return psd_ref + psd_ref*(fmin-f)*np.exp(-(fmin-f))/3

psd_dict = {}
for ifo in ifos:
    psd = np.zeros(len(freqs))
    for i,f in enumerate(freqs):
        if f >= fmin:
            psd[i] = psd_func_dict[ifo](f)
        else:
            psd[i] = pad_low_freqs(f, psd_func_dict[ifo](fmin))
    psd_dict[ifo] = psd

rng = np.random.default_rng(12345)

noise_fd_dict = {}
for ifo, psd in psd_dict.items():
    var = psd / (4.*delta_f)  # this is the variance of LIGO noise given the definition of the likelihood function
    noise_real = rng.normal(size=len(psd), loc=0, scale=np.sqrt(var))
    noise_imag = rng.normal(size=len(psd), loc=0, scale=np.sqrt(var))
    noise_fd_dict[ifo] = noise_real + 1j*noise_imag

# These are the parameters of the injected signal
m1 = 35.0
m2 = 30.0
Mc, eta = ms_to_Mc_eta(jnp.array([m1, m2]))
chi1 = 0.4
chi2 = -0.3
dist_mpc = 1000.0
tc = 2.0
phic = np.pi/4
inclination = 1.57*np.pi/8
polarization_angle = 1.2*np.pi/8
ra = 0.3
dec = 0.5



H1 = get_H1()
H1_response = make_detector_response(H1[0], H1[1])
L1 = get_L1()
L1_response = make_detector_response(L1[0], L1[1])


def gen_waveform_H1(f, theta):
    theta_waveform = theta[:9]
    ra = theta[9]
    dec = theta[10]
    hp, hc = gen_IMRPhenomD_polar(f, theta_waveform)
    return H1_response(f, hp, hc, ra, dec, theta[5], theta[8])

def gen_waveform_L1(f, theta):
    theta_waveform = theta[:9]
    ra = theta[9]
    dec = theta[10]
    hp, hc = gen_IMRPhenomD_polar(f, theta_waveform)
    return L1_response(f, hp, hc, ra, dec, theta[5], theta[8])

true_param = jnp.array([Mc, eta, chi1, chi2, dist_mpc, tc, phic, inclination, polarization_angle, ra, dec])


f_list = freqs[freqs>fmin]
H1_signal = gen_waveform_H1(f_list, true_param)
H1_noise_psd = noise_fd_dict['H1'][freqs>fmin]
H1_data = H1_noise_psd + H1_signal

L1_signal = gen_waveform_L1(f_list, true_param)
L1_noise_psd = noise_fd_dict['L1'][freqs>fmin]
L1_data = L1_noise_psd + L1_signal


# @jax.jit
# def LogLikelihood(theta):
#     h_test = gen_waveform(f_list,theta)
#     df = f_list[1] - f_list[0]
#     match_filter_SNR = 4*jnp.sum((jnp.conj(h_test)*data)/noise_psd*df).real
#     optimal_SNR = 4*jnp.sum((jnp.conj(h_test)*h_test)/noise_psd*df).real
#     return (match_filter_SNR-optimal_SNR/2)

ref_param = jnp.array([Mc, eta, chi1, chi2, dist_mpc, tc, phic, inclination, polarization_angle, ra, dec])

H1_logL = make_heterodyne_likelihood(H1_data, gen_waveform_H1, ref_param, psd_dict['H1'], f_list, 101)
L1_logL = make_heterodyne_likelihood(L1_data, gen_waveform_L1, ref_param, psd_dict['L1'], f_list, 101)

n_dim = 11
n_chains = 1000
n_loop = 5
n_local_steps = 1000
n_global_steps = 1000
learning_rate = 0.01
max_samples = 50000
momentum = 0.9
num_epochs = 1000
batch_size = 50000
stepsize = 0.01

guess_param = np.array(jnp.repeat(true_param[None,:],int(n_chains),axis=0)*np.random.normal(loc=1,scale=0.01,size=(int(n_chains),n_dim)))
guess_param[guess_param[:,1]>0.25,1] = 0.249
guess_param[:,6] = (guess_param[:,6]+np.pi/2)%(np.pi)-np.pi/2
guess_param[:,7] = (guess_param[:,7]+np.pi/2)%(np.pi)-np.pi/2


print("Preparing RNG keys")
rng_key_set = initialize_rng_keys(n_chains, seed=42)

print("Initializing MCMC model and normalizing flow model.")

prior_range = jnp.array([[10,70],[0.0,0.25],[-1,1],[-1,1],[0,2000],[-5,5],[-np.pi/2,np.pi/2],[-np.pi/2,np.pi/2],[0,2*np.pi],[0,2*np.pi],[0,np.pi]])

initial_position = jax.random.uniform(rng_key_set[0], shape=(int(n_chains), n_dim)) * 1
for i in range(n_dim):
    initial_position = initial_position.at[:,i].set(initial_position[:,i]*(prior_range[i,1]-prior_range[i,0])+prior_range[i,0])

initial_position = initial_position.at[:,0].set(guess_param[:,0])
initial_position = initial_position.at[:,1].set(guess_param[:,1])
for i in range(2,11):
    initial_position = initial_position.at[:int(n_chains/10),i].set(guess_param[:int(n_chains/10),i])

initial_position = initial_position.at[:,5].set(guess_param[:,5])

def top_hat(x):
    output = 0.
    for i in range(n_dim):
        output = jax.lax.cond(x[i]>=prior_range[i,0], lambda: output, lambda: -jnp.inf)
        output = jax.lax.cond(x[i]<=prior_range[i,1], lambda: output, lambda: -jnp.inf)
    return output

def posterior(theta):
    prior = top_hat(theta)
    return H1_logL(theta) + L1_logL(theta) + prior


# # L1 = jax.vmap(LogLikelihood)(guess_param)
# # L2 = jax.vmap(jax.jit(logL))(guess_param)





model = RealNVP(10, n_dim, 64, 1)

print("Initializing sampler class")

posterior = posterior
dposterior = jax.grad(posterior)

mass_matrix = jnp.eye(n_dim)
mass_matrix = mass_matrix.at[1,1].set(1e-3)
mass_matrix = mass_matrix.at[5,5].set(1e-3)

local_sampler,updater, kernel, logp, dlogp = make_mala_sampler(posterior, dposterior,5e-3, jit=True, M=mass_matrix)

# print("Precompling")
# from time import time

# current_time = time()
# logp(initial_position)
# dlogp(initial_position)
# kernel(rng_key_set[1], initial_position, logp(initial_position))
# print("Precompling time: ", time()-current_time)

print("Running sampler")

nf_sampler = Sampler(n_dim, rng_key_set, model, local_sampler,
                    posterior,
                    d_likelihood=dposterior,
                    n_loop=n_loop,
                    n_local_steps=n_local_steps,
                    n_global_steps=n_global_steps,
                    n_chains=n_chains,
                    stepsize=stepsize,
                    n_nf_samples=100,
                    learning_rate=learning_rate,
                    n_epochs= num_epochs,
                    max_samples = max_samples,
                    momentum=momentum,
                    batch_size=batch_size,
                    use_global=True,)

nf_sampler.sample(initial_position)
