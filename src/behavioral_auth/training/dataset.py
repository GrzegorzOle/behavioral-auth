import json
import numpy as np

def load_training_dataset(conn):
    rows = conn.execute('SELECT data_json FROM fused_sequences ORDER BY seq_end_ns').fetchall()
    if not rows:
        raise SystemExit('No fused_sequences. Run features pipeline first.')
    return np.array([json.loads(r[0]) for r in rows], dtype=np.float32)
