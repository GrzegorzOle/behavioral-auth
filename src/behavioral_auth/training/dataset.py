"""Training dataset loader.

Reads all fused_sequences rows from DuckDB and returns a NumPy array
of shape (n_sequences, seq_len, n_features) ready for the training loop.
"""

import json
import numpy as np


def load_training_dataset(conn) -> 'np.ndarray':
    """Load all fused sequences from DuckDB into a 3-D NumPy array.

    Args:
        conn: Open DuckDB connection.

    Returns:
        float32 array of shape (n_sequences, seq_len, n_features).

    Raises:
        SystemExit: If no sequences are found (feature pipeline not run yet).
    """
    rows = conn.execute(
        'SELECT data_json FROM fused_sequences ORDER BY seq_end_ns'
    ).fetchall()
    if not rows:
        raise SystemExit('No fused_sequences found. Run: behavioral-features')
    return np.array([json.loads(r[0]) for r in rows], dtype=np.float32)
