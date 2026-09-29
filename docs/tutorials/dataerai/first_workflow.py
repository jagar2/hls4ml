"""Fit a synthetic linear model and record its path to generated HLS C++."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from hls4ml.model.graph import ModelGraph
from hls4ml.provenance import add_arguments, workflow
from hls4ml.utils.config import create_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, help='Source checkout; overrides --dataerai-repo')
    parser.add_argument('--output', type=Path, default=Path('.dataerai/tutorial'))
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--fail-after-fit', action='store_true')
    add_arguments(parser, enabled=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    journal = output / 'run.jsonl'

    output.mkdir(parents=True, exist_ok=True)
    with workflow(
        args,
        'Synthetic linear model tutorial',
        repository=args.repo,
        journal=journal,
        parameters={'seed': args.seed, 'samples': 32, 'synthetic': True},
    ) as run:
        with run.step('prepare_dataset', parameters={'seed': args.seed}, code=__file__):
            generator = np.random.default_rng(args.seed)
            x = generator.uniform(-1, 1, size=(32, 2))
            y = x @ np.array([0.75, -0.25]) + 0.1
            np.savez(output / 'dataset.npz', x=x, y=y)
            dataset = run.artifact(output / 'dataset.npz')

        with run.step(
            'fit_linear_model', inputs=[dataset], parameters={'method': 'numpy.linalg.lstsq'}, code=__file__
        ) as record:
            design = np.column_stack([x, np.ones(len(x))])
            coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
            weight = coefficients[:2].reshape(2, 1)
            bias = coefficients[2:]
            np.savez(output / 'model.npz', weight=weight, bias=bias)
            model_record = run.artifact(output / 'model.npz')
            record['weight'] = weight
            record['bias'] = bias

        if args.fail_after_fit:
            with run.step('intentional_failure', inputs=[model_record]):
                raise RuntimeError('Intentional tutorial failure after the model was recorded')

        with run.step('evaluate_numpy_fit', inputs=[dataset, model_record]) as record:
            mse = float(np.mean((x @ weight[:, 0] + bias[0] - y) ** 2))
            metrics = {'numpy_training_mse': mse, 'synthetic': True, 'hls_inference_measured': False}
            (output / 'metrics.json').write_text(json.dumps(metrics, indent=2) + '\n')
            record['metrics'] = metrics
            run.artifact(output / 'metrics.json')

        with run.step('generate_hls', inputs=[model_record], parameters={'backend': 'Vivado'}, code=__file__):
            config = create_config(output_dir=str(output / 'hls'), project_name='linear_tutorial', backend='Vivado')
            config['HLSConfig'] = {'Model': {'Precision': 'fixed<16,6>', 'ReuseFactor': 1}}
            graph = ModelGraph.from_layer_list(
                config,
                [
                    {'name': 'features', 'class_name': 'InputLayer', 'input_shape': [2]},
                    {
                        'name': 'linear',
                        'class_name': 'Dense',
                        'n_in': 2,
                        'n_out': 1,
                        'weight_data': weight,
                        'bias_data': bias,
                    },
                ],
            )
            graph.write()

    print(json.dumps({'journal': str(run.path) if run.id else None, 'output': str(output), 'metrics': metrics}))


if __name__ == '__main__':
    main()
