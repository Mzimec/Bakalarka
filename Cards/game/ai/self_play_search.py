"""Root-only Dirichlet noise; no worker pools or changes at interior nodes."""
from game.ai.action_priors import normalize_prior


class SelfPlaySearch:
    """!
    @brief Decorate search with self-play behavior under the same information boundary.
    """
    def __init__(self, search, *, rng, alpha=.3, epsilon=.25):
        self.searcher, self.rng, self.alpha, self.epsilon = search,rng,alpha,epsilon

    def __getattr__(self, name):
        return getattr(self.searcher,name)

    def search(self, factory, information, **kwargs):
        """!
        @brief Run bounded search and return a detached recommendation with diagnostics.
        """
        base = self.searcher
        previous = base.action_prior
        if base.selection == 'uct' or not self.epsilon:
            return base.search(factory,information,**kwargs)
        noise = [self.rng.gammavariate(self.alpha,1.) for _ in information.actions]
        total = sum(noise)
        if not total:
            raise ValueError('Degenerate Dirichlet sample')
        root = information.fast_key
        def prior(info):
            probabilities = normalize_prior(info,previous(info))
            if info.fast_key != root:
                return probabilities
            return {a:(1-self.epsilon)*probabilities[a]+self.epsilon*n/total
                    for a,n in zip(info.actions,noise)}
        base.action_prior = prior
        try:
            return base.search(factory,information,**kwargs)
        finally:
            base.action_prior = previous
