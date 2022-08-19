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


from flowMC.nfmodel.realNVP import RealNVP
from flowMC.sampler.MALA import make_mala_sampler
from flowMC.sampler.Sampler import Sampler
from flowMC.utils.PRNG_keys import initialize_rng_keys
from flowMC.nfmodel.utils import *

import matplotlib.pyplot as plt

psd_func_dict = {
    'H1': lalsim.SimNoisePSDaLIGOZeroDetHighPower,
    'L1': lalsim.SimNoisePSDaLIGOZeroDetHighPower,
    'V1': lalsim.SimNoisePSDAdvVirgo,
}
ifos = list(psd_func_dict.keys())

# define center of time array
tgps_geo = 1126259462.423

# define sampling rate and duration
fsamp = 8192
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
m1 = 50.0
m2 = 10.0
Mc, eta = ms_to_Mc_eta(jnp.array([m1, m2]))
chi1 = 0.4
chi2 = -0.3
dist_mpc = 1000.0
tc = 2.0
phic = 0.0
inclination = np.pi
polarization_angle = np.pi/2
ra = 0.3
dec = 0.5

n_chains = 100

detector_presets = {'H1': get_H1()}

theta_ripple = jnp.array([Mc, eta, chi1, chi2, dist_mpc, tc, phic, inclination, polarization_angle])
theta_ripple_vec = np.array(jnp.repeat(theta_ripple[None,:],n_chains,axis=0)*np.random.normal(loc=1,scale=0.01,size=(n_chains,9)))
theta_ripple_vec[theta_ripple_vec[:,1]>0.25,1] = 0.25

f_list = freqs[freqs>fmin]
hp = gen_IMRPhenomD_polar(f_list, theta_ripple)
noise_psd = psd[freqs>fmin]
data = noise_psd + hp[0]


@jax.jit
def LogLikelihood(theta):
    h_test = gen_IMRPhenomD_polar(f_list, theta)
    df = f_list[1] - f_list[0]
    match_filter_SNR = 4*jnp.sum((jnp.conj(h_test[0])*data)/noise_psd*df).real
    optimal_SNR = 4*jnp.sum((jnp.conj(h_test[0])*h_test[0])/noise_psd*df).real
    return (-match_filter_SNR+optimal_SNR/2)

theta_ref = jnp.array([Mc, 0.138, chi1, chi2, dist_mpc, tc, phic, inclination, polarization_angle])

h_function = lambda f,theta:gen_IMRPhenomD_polar(f,theta)[0]

logpdf = jax.jit(make_heterodyne_likelihood(data, h_function, theta_ref, noise_psd, f_list, 101))
d_logpdf = jax.jit(jax.grad(logpdf))

L1 = jax.vmap(LogLikelihood)(theta_ripple_vec)
L2 = jax.vmap(jax.jit(logpdf))(theta_ripple_vec)


#def mala_kernel(rng_key, position, log_prob, dt=0.1):

dt = 1e-7
def mala_kernel(carry, data):
    rng_key, position, log_prob, do_accept = carry
    rng_key, key1, key2 = jax.random.split(rng_key,3)
    proposal = position + dt * d_logpdf(position)
    proposal += dt * jnp.sqrt(2/dt) * jax.random.normal(key1, shape=position.shape)
    ratio = logpdf(proposal) - logpdf(position)
    ratio -= ((position - proposal - dt * d_logpdf(proposal)) ** 2 / (4 * dt)).sum()
    ratio += ((proposal - position - dt * d_logpdf(position)) ** 2 / (4 * dt)).sum()
    proposal_log_prob = logpdf(proposal)

    log_uniform = jnp.log(jax.random.uniform(key2))
    do_accept = log_uniform < ratio

    position = jax.lax.cond(do_accept, lambda: proposal, lambda: position)
    log_prob = jax.lax.cond(do_accept, lambda: proposal_log_prob, lambda: log_prob)
    return (rng_key, position, log_prob, do_accept), (position, log_prob, do_accept)

mala_kernel = jax.jit(mala_kernel)
state = (jax.random.PRNGKey(1),theta_ripple, logpdf(theta_ripple), False)
# jax.lax.scan(mala_kernel, state, jax.random.split(jax.random.PRNGKey(1),10))
def mala_update(rng_key, position, logpdf, n_steps=100):
    carry = (rng_key, position, logpdf, False)
    y = jax.lax.scan(mala_kernel, carry, jax.random.split(rng_key,n_steps))
    return y

with jax.profiler.trace("./", create_perfetto_link=True):
    mala_update = jax.jit(jax.vmap(mala_update))
    result = mala_update(jax.random.split(jax.random.PRNGKey(1),100), theta_ripple_vec, jax.vmap(logpdf)(theta_ripple_vec))