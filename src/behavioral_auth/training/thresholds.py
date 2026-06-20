import numpy as np

def calculate_thresholds(errors):
    return float(np.percentile(errors, 95)), float(np.percentile(errors, 99))
