import jax
import jax.numpy as jnp
from flowMC.nfmodel.base import Distribution
from jaxtyping import Array, Float
from typing import Callable, Union
from dataclasses import field

class Prior(Distribution):
    r"""A thin wrapper build on top of flowMC distributions to do book keeping.

    Should not be used directly since it does not implement any of the real method.
    """

    naming: list[str]
    transforms: list[Callable] = field(default_factory=dict)

    @property
    def n_dim(self):
        return len(self.naming)

    def __init__(self, naming: list[str], transforms: dict[Callable] = {}):
        self.naming = naming
        self.transforms = []
        for name in naming:
            if name in transforms:
                self.transforms.append(transforms[name])
            else:
                self.transforms.append(lambda x: x)

    def transform(self, x: Array) -> Array:
        for i,transform in enumerate(self.transforms):
            x = x.at[i].set(transform(x[i]))
        return x


class Uniform(Prior):

    xmin: Array
    xmax: Array

    def __init__(self, xmin: Union[float,Array], xmax: Union[float,Array], **kwargs):
        super().__init__(kwargs.get("naming"), kwargs.get("transforms"))
        self.xmax = jnp.array(xmax)
        self.xmin = jnp.array(xmin)
    
    def sample(self, rng_key: jax.random.PRNGKey, n_samples: int) -> Array:
        samples = jax.random.uniform(rng_key, (n_samples,self.n_dim), minval=self.xmin, maxval=self.xmax)
        return samples # TODO: remember to cast this to a named array

    def log_prob(self, x: Array) -> Float:
        return jnp.sum(jnp.log(1./(self.xmax-self.xmin))) 
