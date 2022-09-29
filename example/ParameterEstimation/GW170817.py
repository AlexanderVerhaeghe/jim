import numpy as np
import jax.numpy as jnp
import jax

from ripple.waveforms.IMRPhenomD import gen_IMRPhenomD_polar
from jaxgw.PE.detector_preset import * 
from jaxgw.PE.heterodyneLikelihood import make_heterodyne_likelihood_mutliple_detector
from jaxgw.PE.detector_projection import make_detector_response

from flowMC.nfmodel.rqSpline import RQSpline
from flowMC.sampler.MALA import make_mala_sampler
from flowMC.sampler.Sampler import Sampler
from flowMC.utils.PRNG_keys import initialize_rng_keys
from flowMC.nfmodel.utils import *

data = np.load('./data/GW170817_data.npz',allow_pickle=True)

minimum_frequency = 20

H1_frequency = data['frequency']
H1_data = data['data'].tolist()['H1'][H1_frequency>minimum_frequency]
H1_psd = data['psd'].tolist()['H1'][H1_frequency>minimum_frequency]
H1_frequency = H1_frequency[H1_frequency>minimum_frequency]

L1_frequency = data['frequency']
L1_data = data['data'].tolist()['L1'][L1_frequency>minimum_frequency]
L1_psd = data['psd'].tolist()['L1'][L1_frequency>minimum_frequency]
L1_frequency = L1_frequency[L1_frequency>minimum_frequency]

V1_frequency = data['frequency']
V1_data = data['data'].tolist()['V1'][V1_frequency>minimum_frequency]
V1_psd = data['psd'].tolist()['V1'][V1_frequency>minimum_frequency]
V1_frequency = V1_frequency[V1_frequency>minimum_frequency]

H1 = get_H1()
H1_response = make_detector_response(H1[0], H1[1])
L1 = get_L1()
L1_response = make_detector_response(L1[0], L1[1])
V1 = get_V1()
V1_response = make_detector_response(V1[0], V1[1])

def gen_data(theta):
    ra = theta[9]
    dec = theta[10]
    hp_test, hc_test = gen_IMRPhenomD_polar(H1_frequency, theta[:9])
    h_test_H1 = H1_response(H1_frequency, hp_test, hc_test, ra, dec, theta[5], theta[8])
    h_test_L1 = L1_response(L1_frequency, hp_test, hc_test, ra, dec, theta[5], theta[8])
    h_test_V1 = V1_response(V1_frequency, hp_test, hc_test, ra, dec, theta[5], theta[8])
    return h_test_H1, h_test_L1, h_test_V1

def LogLikelihood(theta):
    ra = theta[9]
    dec = theta[10]
    hp_test, hc_test = gen_IMRPhenomD_polar(H1_frequency, theta[:9])
    h_test_H1 = H1_response(H1_frequency, hp_test, hc_test, ra, dec, theta[5], theta[8])
    h_test_L1 = L1_response(L1_frequency, hp_test, hc_test, ra, dec, theta[5], theta[8])
    h_test_V1 = V1_response(V1_frequency, hp_test, hc_test, ra, dec, theta[5], theta[8])
    df = H1_frequency[1] - H1_frequency[0]
    match_filter_SNR_H1 = 4*jnp.sum((jnp.conj(h_test_H1)*H1_data)/H1_psd*df).real
    match_filter_SNR_L1 = 4*jnp.sum((jnp.conj(h_test_L1)*L1_data)/L1_psd*df).real
    match_filter_SNR_V1 = 4*jnp.sum((jnp.conj(h_test_V1)*V1_data)/V1_psd*df).real
    optimal_SNR_H1 = 4*jnp.sum((jnp.conj(h_test_H1)*h_test_H1)/H1_psd*df).real
    optimal_SNR_L1 = 4*jnp.sum((jnp.conj(h_test_L1)*h_test_L1)/L1_psd*df).real
    optimal_SNR_V1 = 4*jnp.sum((jnp.conj(h_test_V1)*h_test_V1)/V1_psd*df).real

    return (match_filter_SNR_H1-optimal_SNR_H1/2) + (match_filter_SNR_L1-optimal_SNR_L1/2) + (match_filter_SNR_V1-optimal_SNR_V1/2)

ref_param = jnp.array([ 1.19787692,  0.23      ,  0.38364522,  0.05033383, 17.03779457,
       31.5087331 ,  1.54276628,  1.17534099,  1.6750658 ,  5.12657835,
       -0.40019376])

# ref_param = jnp.array([ 1.17.,  0.249,  0.,  0., 34.19898126,
#                 31,  4.42730312,  2.56555149,  1.50688411,  2.62855454,
# 0.1451079 ])

ref_param = ref_param.at[-1].set(ref_param[-1]%(jnp.pi))
ref_param = ref_param.at[6].set((ref_param[6]+jnp.pi/2)%(jnp.pi)-jnp.pi/2)

data_list = [H1_data, L1_data, V1_data]
psd_list = [H1_psd, L1_psd, V1_psd]
response_list = [H1_response, L1_response, V1_response]

logL = make_heterodyne_likelihood_mutliple_detector(data_list, psd_list, response_list, gen_IMRPhenomD_polar, ref_param, H1_frequency, 101)


n_dim = 11
n_chains = 1000
n_loop_training = 10
n_loop_production = 10
n_local_steps = 1000
n_global_steps = 1000
learning_rate = 0.001
max_samples = 50000
momentum = 0.9
num_epochs = 300
batch_size = 50000

guess_param = ref_param

guess_param = np.array(jnp.repeat(guess_param[None,:],int(n_chains),axis=0)*np.random.normal(loc=1,scale=0.001,size=(int(n_chains),n_dim)))
guess_param[guess_param[:,1]>0.25,1] = 0.249
guess_param[:,6] = (guess_param[:,6]+np.pi/2)%(np.pi)-np.pi/2
guess_param[:,7] = (guess_param[:,7]+np.pi/2)%(np.pi)-np.pi/2


print("Preparing RNG keys")
rng_key_set = initialize_rng_keys(n_chains, seed=42)

print("Initializing MCMC model and normalizing flow model.")

prior_range = jnp.array([[1.1,2.5],[0.20,0.25],[0,0.85],[0,0.85],[0,200],[28,35],[0,2*np.pi],[0,np.pi],[0,np.pi],[0,2*np.pi],[-np.pi/2,np.pi/2]])

initial_position = jax.random.uniform(rng_key_set[0], shape=(int(n_chains), n_dim)) * 1
for i in range(n_dim):
    initial_position = initial_position.at[:,i].set(initial_position[:,i]*(prior_range[i,1]-prior_range[i,0])+prior_range[i,0])

initial_position = initial_position.at[:,0].set(guess_param[:,0])
initial_position = initial_position.at[:,1].set(guess_param[:,1])
initial_position = initial_position.at[:,5].set(guess_param[:,5])

def top_hat(x):
    output = 0.
    for i in range(n_dim):
        output = jax.lax.cond(x[i]>=prior_range[i,0], lambda: output, lambda: -jnp.inf)
        output = jax.lax.cond(x[i]<=prior_range[i,1], lambda: output, lambda: -jnp.inf)
    return output

def posterior(theta):
    prior = top_hat(theta)
    return logL(theta) + prior

model = RQSpline(n_dim, 10, [128,128], 8)

print("Initializing sampler class")

posterior = posterior

mass_matrix = jnp.eye(n_dim)
mass_matrix = mass_matrix.at[0,0].set(1e-5)
mass_matrix = mass_matrix.at[1,1].set(1e-4)
mass_matrix = mass_matrix.at[2,2].set(1e-3)
mass_matrix = mass_matrix.at[2,2].set(1e-3)
mass_matrix = mass_matrix.at[5,5].set(1e-5)
mass_matrix = mass_matrix.at[9,9].set(1e-2)
mass_matrix = mass_matrix.at[10,10].set(1e-2)

local_sampler_caller = lambda x: make_mala_sampler(x, jit=True)
sampler_params = {'dt':mass_matrix*3e-3}
print("Running sampler")

nf_sampler = Sampler(
    n_dim,
    rng_key_set,
    local_sampler_caller,
    sampler_params,
    posterior,
    model,
    n_loop_training=n_loop_training,
    n_loop_production = n_loop_production,
    n_local_steps=n_local_steps,
    n_global_steps=n_global_steps,
    n_chains=n_chains,
    n_epochs=num_epochs,
    learning_rate=learning_rate,
    momentum=momentum,
    batch_size=batch_size,
    use_global=True,
    keep_quantile=0.,
)

nf_sampler.sample(initial_position)
