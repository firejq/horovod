# Copyright 2020 Uber Technologies, Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import

from distutils.version import LooseVersion

import tensorflow as tf

from tensorflow.python.framework import ops

import horovod.tensorflow as _hvd

from horovod.common.elastic import run_fn, ObjectState


_IS_TF2 = LooseVersion(tf.__version__) >= LooseVersion('2.0.0')


def run(func):
    return run_fn(func, _hvd)


def _broadcast_model(model, optimizer, backend):
    if _hvd._executing_eagerly():
        # TensorFlow 2.0 or TensorFlow eager
        _hvd.broadcast_variables(model.variables, root_rank=0)
        _hvd.broadcast_variables(optimizer.variables(), root_rank=0)
    else:
        bcast_op = _hvd.broadcast_global_variables(0)
        backend.get_session().run(bcast_op)


def _model_built(model):
    return model.built if hasattr(model, 'build') else True


def _global_variables():
    return tf.global_variables() if not _IS_TF2 else tf.compat.v1.global_variables()


def _default_session():
    return ops.get_default_session() if not _IS_TF2 else None


class TensorFlowKerasState(ObjectState):
    def __init__(self, model, optimizer=None, backend=None, **kwargs):
        self.model = model
        if not _model_built(model):
            raise ValueError('Model must be built first. Run `model.build(input_shape)`.')

        self.optimizer = optimizer or model.optimizer
        self.backend = backend
        self._save_model()

        super(TensorFlowKerasState, self).__init__(_hvd.broadcast_object, **kwargs)

    def save(self):
        self._save_model()
        super(TensorFlowKerasState, self).save()

    def restore(self):
        self._load_model()
        super(TensorFlowKerasState, self).restore()

    def sync(self):
        _broadcast_model(self.model, self.optimizer, backend=self.backend)
        self._save_model()
        super(TensorFlowKerasState, self).sync()

    def _save_model(self):
        self._saved_model_state = self.model.get_weights()
        self._saved_optimizer_state = self.optimizer.get_weights()

    def _load_model(self):
        self.model.set_weights(self._saved_model_state)
        self.optimizer.set_weights(self._saved_optimizer_state)


class TensorFlowState(ObjectState):
    def __init__(self, variables=None, session=None, **kwargs):
        self.variables = variables or _global_variables()
        self.session = session or _default_session()
        self._eval_fn = self._to_numpy if _hvd._executing_eagerly() else self._eval_var
        self._assign_fn = self._assign_var if _IS_TF2 else self._load_var
        self._save_model()

        super(TensorFlowState, self).__init__(_hvd.broadcast_object, **kwargs)

    def save(self):
        self._save_model()
        super(TensorFlowState, self).save()

    def restore(self):
        self._load_model()
        super(TensorFlowState, self).restore()

    def sync(self):
        bcast_op = _hvd.broadcast_variables(self.variables, root_rank=0)
        if self.session is not None:
            self.session.run(bcast_op)
        self._save_model()
        super(TensorFlowState, self).sync()

    def _save_model(self):
        self._values = [self._eval_fn(var) for var in self.variables]

    def _eval_var(self, var):
        return var.eval(self.session)

    def _to_numpy(self, var):
        return var.numpy()

    def _load_model(self):
        for var, value in zip(self.variables, self._values):
            self._assign_fn(var, value)

    def _load_var(self, var, value):
        var.load(value, self.session)

    def _assign_var(self, var, value):
        var.assign(value)