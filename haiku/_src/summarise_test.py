# Copyright 2020 DeepMind Technologies Limited. All Rights Reserved.
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
"""Tests for haiku._src.summarise."""
# pylint: disable=unnecessary-lambda

from typing import Sequence

from absl.testing import absltest
from absl.testing import parameterized
from haiku._src import base
from haiku._src import basic
from haiku._src import module as module_lib
from haiku._src import summarise
from haiku._src import transform
import jax.numpy as jnp


def tabulate_to_list(
    f,
    *args,
    columns=None,
    filters=None,
) -> Sequence[Sequence[str]]:
  old_tabulate = summarise.tabulate_lib.tabulate
  summarise.tabulate_lib.tabulate = lambda rows, **_: rows
  try:
    out = summarise.tabulate(f, columns=columns, filters=filters)(*args)
  finally:
    summarise.tabulate_lib.tabulate = old_tabulate
  if out == "No modules matching filters.":
    return []
  else:
    return out


def get_summary(f, *args):
  return summarise.eval_summary(f)(*args)


class SummariseTest(parameterized.TestCase):

  def test_empty(self):
    self.assertEmpty(get_summary(lambda: None))

  def test_filters_ctor_only(self):
    f = lambda: IdentityModule()  # NOTE: Just calling ctor.
    self.assertEmpty(get_summary(f))

  @parameterized.parameters(*range(1, 5))
  def test_one_row_per_method_call(self, num_calls):
    def f():
      m = IdentityModule()
      for _ in range(num_calls):
        m(x)

    x = jnp.ones([])
    invocations = get_summary(f)
    self.assertLen(invocations, num_calls)
    for invocation in invocations[1:]:
      self.assertEqual(invocations[0].context.method_name,
                       invocation.context.method_name)


class TabulateTest(parameterized.TestCase):

  def test_filters_no_output(self):
    f = lambda: NoOutputModule()()
    self.assertEmpty(tabulate_to_list(f))

  def test_filters_no_param_modules(self):
    f = lambda: NoParamsModule()(2)
    self.assertEmpty(tabulate_to_list(f))

  def test_module_column(self):
    def f():
      IdentityModule(name="foo")(1)
      IdentityModule(name="bar")(1)
    rows = tabulate_to_list(f, columns=("module",))
    expected = [["foo (IdentityModule)"],
                ["bar (IdentityModule)"]]
    self.assertEqual(rows, expected)

  def test_config_column(self):
    def f():
      IdentityModule(name="foo")(1)
      IdentityModule(name="bar")(1)
    rows = tabulate_to_list(f, columns=("config",))
    expected = [["IdentityModule(name='foo')"],
                ["IdentityModule(name='bar')"]]
    self.assertEqual(rows, expected)

  def test_owned_params_column(self):
    f = lambda: CallsOtherModule(MultipleParametersModule())()
    rows = tabulate_to_list(f, columns=("owned_params",))
    expected = [[""],
                ["b: f32[40,50,60]\n"
                 "w: f32[10,20,30]"]]
    self.assertEqual(rows, expected)

  def test_owned_params_sorted_by_size_then_name(self):
    def f():
      base.get_parameter("a", [1], init=jnp.zeros)
      base.get_parameter("b", [2], init=jnp.zeros)
      base.get_parameter("c", [2], init=jnp.zeros)
      base.get_parameter("d", [3], init=jnp.zeros)
      return 0

    f = lambda f=f: basic.to_module(f)()()
    rows = tabulate_to_list(f, columns=("owned_params",))
    expected = [["d: f32[3]\n"
                 "b: f32[2]\n"
                 "c: f32[2]\n"
                 "a: f32[1]"]]
    self.assertEqual(rows, expected)

  def test_output_column_structured(self):
    f = lambda: IdentityModule()({"a": jnp.ones([32, 32]),  # pylint: disable=g-long-lambda
                                  "b": [jnp.zeros([1]),
                                        jnp.zeros([], jnp.int16)]})
    rows = tabulate_to_list(f, columns=("output",))
    expected = [["{'a': f32[32,32], 'b': [f32[1], s16[]]}"]]
    self.assertEqual(rows, expected)

  def test_params_size_column(self):
    f = lambda: CallsOtherModule(MultipleParametersModule())()
    rows = tabulate_to_list(f, columns=("params_size",))
    size = "126,000"
    expected = [[size], [size]]
    self.assertEqual(rows, expected)

  def test_params_bytes_column(self):
    f = lambda: CallsOtherModule(MultipleParametersModule())()
    rows = tabulate_to_list(f, columns=("params_bytes",))
    size = "504.00 KB"
    expected = [[size], [size]]
    self.assertEqual(rows, expected)

  def test_invalid_column(self):
    with self.assertRaisesRegex(ValueError, "Invalid column.*nonsense"):
      tabulate_to_list(lambda: None, columns=("nonsense",))

  def test_invalid_filter(self):
    with self.assertRaisesRegex(ValueError, "Invalid filter.*nonsense"):
      tabulate_to_list(lambda: None, filters=("nonsense",))

  def test_f_accepts_tabulate_kwargs(self):
    tabulate_kwargs = {"tablefmt": "html"}
    f = lambda: CallsOtherModule(MultipleParametersModule())()
    output = summarise.tabulate(f, tabulate_kwargs=tabulate_kwargs)()
    self.assertIn("<table>", output)

  def test_equivalent_when_passing_transformed_fn(self):
    f = lambda: CallsOtherModule(MultipleParametersModule())()
    f_transform = transform.transform(f)
    rows = tabulate_to_list(f)
    self.assertEqual(rows, tabulate_to_list(f_transform))
    self.assertEqual(rows, tabulate_to_list(f_transform.init))
    self.assertEqual(rows, tabulate_to_list(f_transform.apply))


class MultipleParametersModule(module_lib.Module):

  def __call__(self):
    base.get_parameter("w", [10, 20, 30], init=jnp.zeros)
    base.get_parameter("b", [40, 50, 60], init=jnp.zeros)
    return 1


class IdentityModule(module_lib.Module):

  def __call__(self, x):
    base.get_parameter("w", [], init=jnp.zeros)
    return x


class NoParamsModule(module_lib.Module):

  def __call__(self, x):
    base.get_state("s", [], init=jnp.zeros)
    return x ** 2


class NoOutputModule(module_lib.Module):

  def __call__(self):
    base.get_parameter("w", [], init=jnp.zeros)


class CallsOtherModule(module_lib.Module):

  def __init__(self, other, name=None):
    super().__init__(name=name)
    self.other = other

  def __call__(self, *args):
    return self.other(*args)


if __name__ == "__main__":
  absltest.main()
