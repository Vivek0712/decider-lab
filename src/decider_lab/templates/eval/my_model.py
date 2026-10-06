"""A model of your own, plugged in with {python: "my_model:Heuristic"}.

An adapter gets one decision row and returns one probability per option, in the row's option
order. This one is deliberately simple: it says "yes" a bit more often than "no" and spreads
everything else evenly. Replace predict_one with a call to your model.
"""


class Heuristic:
    def predict_one(self, row):
        n = len(row["options"])
        if row["kind"] == "noul":
            return [0.1, 0.9]  # [P(no), P(yes)]: confidently yes, every time
        return [1.0 / n] * n
