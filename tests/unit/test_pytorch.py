# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.
from __future__ import absolute_import
import logging

import json
import os
import pytest
from mock import ANY, MagicMock, Mock, patch
from packaging.version import Version
import tempfile

from sagemaker import image_uris
from sagemaker.pytorch import defaults
from sagemaker.pytorch import PyTorch, PyTorchPredictor, PyTorchModel
from sagemaker.pytorch.estimator import (
    _get_training_recipe_image_uri,
    _get_training_recipe_gpu_script,
)
from sagemaker.instance_group import InstanceGroup
from sagemaker.session_settings import SessionSettings

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCRIPT_PATH = os.path.join(DATA_DIR, "dummy_script.py")
SERVING_SCRIPT_FILE = "another_dummy_script.py"
MODEL_DATA = "s3://some/data.tar.gz"
ENV = {"DUMMY_ENV_VAR": "dummy_value"}
TIMESTAMP = "2017-11-06-14:14:15.672"
TIME = 1510006209.073025
BUCKET_NAME = "mybucket"
INSTANCE_COUNT = 1
INSTANCE_TYPE = "ml.c4.4xlarge"
INSTANCE_TYPE_GPU = "ml.p4d.24xlarge"
INSTANCE_TYPE_TRAINIUM = "ml.trn1.32xlarge"
ACCELERATOR_TYPE = "ml.eia.medium"
IMAGE_URI = "sagemaker-pytorch"
JOB_NAME = "{}-{}".format(IMAGE_URI, TIMESTAMP)
ROLE = "Dummy"
REGION = "us-west-2"
GPU = "ml.p2.xlarge"
CPU = "ml.c4.xlarge"

ENDPOINT_DESC = {"EndpointConfigName": "test-endpoint"}

ENDPOINT_CONFIG_DESC = {"ProductionVariants": [{"ModelName": "model-1"}, {"ModelName": "model-2"}]}

ENV_INPUT = {"env_key1": "env_val1", "env_key2": "env_val2", "env_key3": "env_val3"}

LIST_TAGS_RESULT = {"Tags": [{"Key": "TagtestKey", "Value": "TagtestValue"}]}

EXPERIMENT_CONFIG = {
    "ExperimentName": "exp",
    "TrialName": "trial",
    "TrialComponentDisplayName": "tc",
    "RunName": "rn",
}

DISTRIBUTION_PYTORCH_DDP_ENABLED = {"pytorchddp": {"enabled": True}}
NEURON_RECIPE = (
    "https://raw.githubusercontent.com/aws-neuron/"
    "neuronx-distributed-training/refs/heads/main/examples/"
    "conf/hf_llama3_8B_config.yaml"
)
RECIPE_GPU_IMAGE = (
    "658645717510.dkr.ecr.us-west-2.amazonaws.com/smdistributed-modelparallel:2.4.1-gpu-py311"
)
RECIPE_NEURON_IMAGE = (
    "763104351884.dkr.ecr.us-west-2.amazonaws.com/"
    "pytorch-training-neuronx:2.1.2-neuronx-py310-sdk2.20.2-ubuntu20.04"
)


@pytest.fixture(name="sagemaker_session")
def fixture_sagemaker_session():
    boto_mock = Mock(name="boto_session", region_name=REGION)
    session = Mock(
        name="sagemaker_session",
        boto_session=boto_mock,
        boto_region_name=REGION,
        config=None,
        local_mode=False,
        s3_resource=None,
        s3_client=None,
        settings=SessionSettings(),
        default_bucket_prefix=None,
    )

    describe = {"ModelArtifacts": {"S3ModelArtifacts": "s3://m/m.tar.gz"}}
    session.sagemaker_client.describe_training_job = Mock(return_value=describe)
    session.sagemaker_client.describe_endpoint = Mock(return_value=ENDPOINT_DESC)
    session.sagemaker_client.describe_endpoint_config = Mock(return_value=ENDPOINT_CONFIG_DESC)
    session.sagemaker_client.list_tags = Mock(return_value=LIST_TAGS_RESULT)
    session.default_bucket = Mock(name="default_bucket", return_value=BUCKET_NAME)
    session.expand_role = Mock(name="expand_role", return_value=ROLE)

    # For tests which doesn't verify config file injection, operate with empty config
    session.sagemaker_config = {}
    return session


@pytest.fixture()
def component_name():
    return "test_component_name"


def _get_full_cpu_image_uri(version, py_version):
    return image_uris.retrieve(
        "pytorch",
        REGION,
        version=version,
        py_version=py_version,
        instance_type=CPU,
        image_scope="training",
    )


def _pytorch_estimator(
    sagemaker_session,
    framework_version,
    py_version,
    instance_type=None,
    base_job_name=None,
    **kwargs,
):
    return PyTorch(
        entry_point=SCRIPT_PATH,
        framework_version=framework_version,
        py_version=py_version,
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=instance_type if instance_type else INSTANCE_TYPE,
        base_job_name=base_job_name,
        **kwargs,
    )


def _create_train_job(version, py_version):
    return {
        "image_uri": _get_full_cpu_image_uri(version, py_version),
        "input_mode": "File",
        "input_config": [
            {
                "ChannelName": "training",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataDistributionType": "FullyReplicated",
                        "S3DataType": "S3Prefix",
                    }
                },
            }
        ],
        "role": ROLE,
        "job_name": JOB_NAME,
        "output_config": {"S3OutputPath": "s3://{}/".format(BUCKET_NAME)},
        "resource_config": {
            "InstanceType": "ml.c4.4xlarge",
            "InstanceCount": 1,
            "VolumeSizeInGB": 30,
        },
        "hyperparameters": {
            "sagemaker_program": json.dumps("dummy_script.py"),
            "sagemaker_container_log_level": str(logging.INFO),
            "sagemaker_job_name": json.dumps(JOB_NAME),
            "sagemaker_submit_directory": json.dumps(
                "s3://{}/{}/source/sourcedir.tar.gz".format(BUCKET_NAME, JOB_NAME)
            ),
            "sagemaker_region": '"us-west-2"',
        },
        "stop_condition": {"MaxRuntimeInSeconds": 24 * 60 * 60},
        "tags": None,
        "vpc_config": None,
        "metric_definitions": None,
        "environment": None,
        "retry_strategy": None,
        "experiment_config": None,
        "enable_network_isolation": False,
        "debugger_hook_config": {
            "CollectionConfigurations": [],
            "S3OutputPath": "s3://{}/".format(BUCKET_NAME),
        },
        "profiler_config": {
            "DisableProfiler": False,
            "S3OutputPath": "s3://{}/".format(BUCKET_NAME),
        },
    }


def _get_environment(submit_directory, model_url, image_uri):
    return {
        "Environment": {
            "SAGEMAKER_SUBMIT_DIRECTORY": submit_directory,
            "SAGEMAKER_PROGRAM": "dummy_script.py",
            "SAGEMAKER_REGION": "us-west-2",
            "SAGEMAKER_CONTAINER_LOG_LEVEL": "20",
        },
        "Image": image_uri,
        "ModelDataUrl": model_url,
    }


@patch("sagemaker.estimator.name_from_base")
def test_create_model(
    name_from_base, sagemaker_session, pytorch_inference_version, pytorch_inference_py_version
):
    container_log_level = '"logging.INFO"'
    source_dir = "s3://mybucket/source"
    base_job_name = "job"

    pytorch = PyTorch(
        entry_point=SCRIPT_PATH,
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        container_log_level=container_log_level,
        base_job_name=base_job_name,
        source_dir=source_dir,
    )

    pytorch.fit(inputs="s3://mybucket/train", job_name="new_name")

    model_name = "model_name"
    name_from_base.return_value = model_name
    model = pytorch.create_model()

    assert model.sagemaker_session == sagemaker_session
    assert model.framework_version == pytorch_inference_version
    assert model.py_version == pytorch_inference_py_version
    assert model.entry_point == SCRIPT_PATH
    assert model.role == ROLE
    assert model.name == model_name
    assert model.container_log_level == container_log_level
    assert model.source_dir == source_dir
    assert model.vpc_config is None

    name_from_base.assert_called_with(base_job_name)


def test_create_model_with_optional_params(
    sagemaker_session, pytorch_inference_version, pytorch_inference_py_version
):
    container_log_level = '"logging.INFO"'
    source_dir = "s3://mybucket/source"
    pytorch = PyTorch(
        entry_point=SCRIPT_PATH,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE,
        container_log_level=container_log_level,
        base_job_name="job",
        source_dir=source_dir,
    )

    pytorch.fit(inputs="s3://mybucket/train", job_name="new_name")

    new_role = "role"
    model_server_workers = 2
    vpc_config = {"Subnets": ["foo"], "SecurityGroupIds": ["bar"]}
    model_name = "model-name"
    model = pytorch.create_model(
        role=new_role,
        model_server_workers=model_server_workers,
        vpc_config_override=vpc_config,
        entry_point=SERVING_SCRIPT_FILE,
        env=ENV,
        name=model_name,
    )

    assert model.role == new_role
    assert model.model_server_workers == model_server_workers
    assert model.vpc_config == vpc_config
    assert model.entry_point == SERVING_SCRIPT_FILE
    assert model.env == ENV
    assert model.name == model_name


@patch("sagemaker.estimator.name_from_base")
def test_create_model_with_custom_image(name_from_base, sagemaker_session):
    container_log_level = '"logging.INFO"'
    source_dir = "s3://mybucket/source"
    image = "pytorch:9000"
    base_job_name = "job"

    pytorch = PyTorch(
        entry_point=SCRIPT_PATH,
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE,
        container_log_level=container_log_level,
        image_uri=image,
        base_job_name=base_job_name,
        source_dir=source_dir,
    )

    pytorch.fit(inputs="s3://mybucket/train", job_name="new_name")

    model_name = "model_name"
    name_from_base.return_value = model_name
    model = pytorch.create_model()

    assert model.sagemaker_session == sagemaker_session
    assert model.image_uri == image
    assert model.entry_point == SCRIPT_PATH
    assert model.role == ROLE
    assert model.name == model_name
    assert model.container_log_level == container_log_level
    assert model.source_dir == source_dir

    name_from_base.assert_called_with(base_job_name)


@patch("sagemaker.utils.repack_model", MagicMock())
@patch("sagemaker.utils.create_tar_file", MagicMock())
@patch("sagemaker.estimator.name_from_base", return_value=JOB_NAME)
@patch("time.time", return_value=TIME)
def test_pytorch(
    time,
    name_from_base,
    sagemaker_session,
    pytorch_inference_version,
    pytorch_inference_py_version,
    gpu_pytorch_instance_type,
):
    pytorch = PyTorch(
        entry_point=SCRIPT_PATH,
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        enable_sagemaker_metrics=False,
    )

    inputs = "s3://mybucket/train"

    pytorch.fit(inputs=inputs, experiment_config=EXPERIMENT_CONFIG)

    sagemaker_call_names = [c[0] for c in sagemaker_session.method_calls]
    assert sagemaker_call_names == ["train", "logs_for_job"]
    boto_call_names = [c[0] for c in sagemaker_session.boto_session.method_calls]
    assert "resource" in boto_call_names

    expected_train_args = _create_train_job(pytorch_inference_version, pytorch_inference_py_version)
    expected_train_args["input_config"][0]["DataSource"]["S3DataSource"]["S3Uri"] = inputs
    expected_train_args["experiment_config"] = EXPERIMENT_CONFIG
    expected_train_args["enable_sagemaker_metrics"] = False

    actual_train_args = sagemaker_session.method_calls[0][2]
    assert actual_train_args == expected_train_args

    model = pytorch.create_model()

    expected_image_uri = image_uris.retrieve(
        "pytorch",
        REGION,
        version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        instance_type=gpu_pytorch_instance_type,
        image_scope="inference",
    )

    actual_environment = model.prepare_container_def(gpu_pytorch_instance_type)
    submit_directory = actual_environment["Environment"]["SAGEMAKER_SUBMIT_DIRECTORY"]
    model_url = actual_environment["ModelDataUrl"]
    expected_environment = _get_environment(submit_directory, model_url, expected_image_uri)
    assert actual_environment == expected_environment

    assert "cpu" in model.prepare_container_def(CPU)["Image"]
    predictor = pytorch.deploy(1, gpu_pytorch_instance_type)
    assert isinstance(predictor, PyTorchPredictor)


@patch("sagemaker.utils.repack_model", MagicMock())
@patch("sagemaker.utils.create_tar_file", MagicMock())
def test_model(
    sagemaker_session,
    pytorch_inference_version,
    pytorch_inference_py_version,
    gpu_pytorch_instance_type,
):
    model = PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        sagemaker_session=sagemaker_session,
    )
    predictor = model.deploy(1, gpu_pytorch_instance_type)
    assert isinstance(predictor, PyTorchPredictor)


@patch("sagemaker.utils.create_tar_file", MagicMock())
@patch("sagemaker.utils.repack_model")
@pytest.mark.parametrize("gpu_pytorch_instance_type", ["1.2"], indirect=True)
def test_mms_model(repack_model, sagemaker_session, gpu_pytorch_instance_type):
    PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        sagemaker_session=sagemaker_session,
        framework_version="1.2",
        py_version="py3",
    ).deploy(1, gpu_pytorch_instance_type)

    repack_model.assert_called_with(
        dependencies=[],
        inference_script=SCRIPT_PATH,
        kms_key=None,
        model_uri="s3://some/data.tar.gz",
        repacked_model_uri=ANY,
        sagemaker_session=sagemaker_session,
        source_directory=None,
    )


@patch("sagemaker.utils.create_tar_file", MagicMock())
@patch("sagemaker.utils.repack_model")
def test_non_mms_model(repack_model, sagemaker_session):
    PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        sagemaker_session=sagemaker_session,
        framework_version="1.1",
        py_version="py3",
    ).deploy(1, GPU)

    repack_model.assert_not_called()


@patch("sagemaker.fw_utils.tar_and_upload_dir", MagicMock())
def test_model_image_accelerator(sagemaker_session):
    with pytest.raises(ValueError) as error:
        model = PyTorchModel(
            MODEL_DATA,
            role=ROLE,
            entry_point=SCRIPT_PATH,
            sagemaker_session=sagemaker_session,
            framework_version="1.3.1",
            py_version="py2",
        )
        model.deploy(1, CPU, accelerator_type=ACCELERATOR_TYPE)
    assert "Unsupported Python version: py2." in str(error)


@patch("sagemaker.utils.create_tar_file", MagicMock())
@patch("sagemaker.utils.repack_model", MagicMock())
def test_model_custom_serialization(
    sagemaker_session,
    pytorch_inference_version,
    pytorch_inference_py_version,
    gpu_pytorch_instance_type,
):
    model = PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        sagemaker_session=sagemaker_session,
    )
    custom_serializer = Mock()
    custom_deserializer = Mock()
    predictor = model.deploy(
        1,
        gpu_pytorch_instance_type,
        serializer=custom_serializer,
        deserializer=custom_deserializer,
    )
    assert isinstance(predictor, PyTorchPredictor)
    assert predictor.serializer is custom_serializer
    assert predictor.deserializer is custom_deserializer


def test_model_prepare_container_def_no_instance_type_or_image():
    model = PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        framework_version="1.3.1",
        py_version="py3",
    )

    with pytest.raises(ValueError) as e:
        model.prepare_container_def()

    expected_msg = "Must supply either an instance type (for choosing CPU vs GPU) or an image URI."
    assert expected_msg in str(e)


def test_attach(sagemaker_session, pytorch_training_version, pytorch_training_py_version):
    training_image = "1.dkr.ecr.us-west-2.amazonaws.com/sagemaker-pytorch:{}-cpu-{}".format(
        pytorch_training_version, pytorch_training_py_version
    )
    returned_job_description = {
        "AlgorithmSpecification": {"TrainingInputMode": "File", "TrainingImage": training_image},
        "HyperParameters": {
            "sagemaker_submit_directory": '"s3://some/sourcedir.tar.gz"',
            "sagemaker_program": '"iris-dnn-classifier.py"',
            "sagemaker_s3_uri_training": '"sagemaker-3/integ-test-data/tf_iris"',
            "sagemaker_container_log_level": '"logging.INFO"',
            "sagemaker_job_name": '"neo"',
            "training_steps": "100",
            "sagemaker_region": '"us-west-2"',
        },
        "RoleArn": "arn:aws:iam::366:role/SageMakerRole",
        "ResourceConfig": {
            "VolumeSizeInGB": 30,
            "InstanceCount": 1,
            "InstanceType": "ml.c4.xlarge",
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": 24 * 60 * 60},
        "TrainingJobName": "neo",
        "TrainingJobStatus": "Completed",
        "TrainingJobArn": "arn:aws:sagemaker:us-west-2:336:training-job/neo",
        "OutputDataConfig": {"KmsKeyId": "", "S3OutputPath": "s3://place/output/neo"},
        "TrainingJobOutput": {"S3TrainingJobOutput": "s3://here/output.tar.gz"},
    }
    sagemaker_session.sagemaker_client.describe_training_job = Mock(
        name="describe_training_job", return_value=returned_job_description
    )

    estimator = PyTorch.attach(training_job_name="neo", sagemaker_session=sagemaker_session)
    assert estimator.latest_training_job.job_name == "neo"
    assert estimator.py_version == pytorch_training_py_version
    assert estimator.framework_version == pytorch_training_version
    assert estimator.role == "arn:aws:iam::366:role/SageMakerRole"
    assert estimator.instance_count == 1
    assert estimator.max_run == 24 * 60 * 60
    assert estimator.input_mode == "File"
    assert estimator.base_job_name == "neo"
    assert estimator.output_path == "s3://place/output/neo"
    assert estimator.output_kms_key == ""
    assert estimator.hyperparameters()["training_steps"] == "100"
    assert estimator.source_dir == "s3://some/sourcedir.tar.gz"
    assert estimator.entry_point == "iris-dnn-classifier.py"


def test_attach_wrong_framework(sagemaker_session):
    rjd = {
        "AlgorithmSpecification": {
            "TrainingInputMode": "File",
            "TrainingImage": "1.dkr.ecr.us-west-2.amazonaws.com/sagemaker-mxnet-py2-cpu:1.0.4",
        },
        "HyperParameters": {
            "sagemaker_submit_directory": '"s3://some/sourcedir.tar.gz"',
            "checkpoint_path": '"s3://other/1508872349"',
            "sagemaker_program": '"iris-dnn-classifier.py"',
            "sagemaker_container_log_level": '"logging.INFO"',
            "training_steps": "100",
            "sagemaker_region": '"us-west-2"',
        },
        "RoleArn": "arn:aws:iam::366:role/SageMakerRole",
        "ResourceConfig": {
            "VolumeSizeInGB": 30,
            "InstanceCount": 1,
            "InstanceType": "ml.c4.xlarge",
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": 24 * 60 * 60},
        "TrainingJobName": "neo",
        "TrainingJobStatus": "Completed",
        "TrainingJobArn": "arn:aws:sagemaker:us-west-2:336:training-job/neo",
        "OutputDataConfig": {"KmsKeyId": "", "S3OutputPath": "s3://place/output/neo"},
        "TrainingJobOutput": {"S3TrainingJobOutput": "s3://here/output.tar.gz"},
    }
    sagemaker_session.sagemaker_client.describe_training_job = Mock(
        name="describe_training_job", return_value=rjd
    )

    with pytest.raises(ValueError) as error:
        PyTorch.attach(training_job_name="neo", sagemaker_session=sagemaker_session)
    assert "didn't use image for requested framework" in str(error)


def test_attach_custom_image(sagemaker_session):
    training_image = "pytorch:latest"
    returned_job_description = {
        "AlgorithmSpecification": {"TrainingInputMode": "File", "TrainingImage": training_image},
        "HyperParameters": {
            "sagemaker_submit_directory": '"s3://some/sourcedir.tar.gz"',
            "sagemaker_program": '"iris-dnn-classifier.py"',
            "sagemaker_s3_uri_training": '"sagemaker-3/integ-test-data/tf_iris"',
            "sagemaker_container_log_level": '"logging.INFO"',
            "sagemaker_job_name": '"neo"',
            "training_steps": "100",
            "sagemaker_region": '"us-west-2"',
        },
        "RoleArn": "arn:aws:iam::366:role/SageMakerRole",
        "ResourceConfig": {
            "VolumeSizeInGB": 30,
            "InstanceCount": 1,
            "InstanceType": "ml.c4.xlarge",
        },
        "StoppingCondition": {"MaxRuntimeInSeconds": 24 * 60 * 60},
        "TrainingJobName": "neo",
        "TrainingJobStatus": "Completed",
        "TrainingJobArn": "arn:aws:sagemaker:us-west-2:336:training-job/neo",
        "OutputDataConfig": {"KmsKeyId": "", "S3OutputPath": "s3://place/output/neo"},
        "TrainingJobOutput": {"S3TrainingJobOutput": "s3://here/output.tar.gz"},
    }
    sagemaker_session.sagemaker_client.describe_training_job = Mock(
        name="describe_training_job", return_value=returned_job_description
    )

    estimator = PyTorch.attach(training_job_name="neo", sagemaker_session=sagemaker_session)
    assert estimator.latest_training_job.job_name == "neo"
    assert estimator.image_uri == training_image
    assert estimator.training_image_uri() == training_image


@patch("sagemaker.pytorch.estimator.python_deprecation_warning")
def test_estimator_py2_warning(warning, sagemaker_session, pytorch_training_version):
    estimator = PyTorch(
        entry_point=SCRIPT_PATH,
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE,
        framework_version=pytorch_training_version,
        py_version="py2",
    )

    assert estimator.py_version == "py2"
    warning.assert_called_with(estimator._framework_name, defaults.LATEST_PY2_VERSION)


@patch("sagemaker.pytorch.model.python_deprecation_warning")
def test_model_py2_warning(warning, sagemaker_session, pytorch_inference_version):
    model = PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        sagemaker_session=sagemaker_session,
        framework_version=pytorch_inference_version,
        py_version="py2",
    )
    assert model.py_version == "py2"
    warning.assert_called_with(model._framework_name, defaults.LATEST_PY2_VERSION)


def test_pt_enable_sm_metrics(
    sagemaker_session, pytorch_training_version, pytorch_training_py_version
):
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_training_version,
        py_version=pytorch_training_py_version,
        enable_sagemaker_metrics=True,
    )
    assert pytorch.enable_sagemaker_metrics


def test_pt_disable_sm_metrics(
    sagemaker_session, pytorch_training_version, pytorch_training_py_version
):
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_training_version,
        py_version=pytorch_training_py_version,
        enable_sagemaker_metrics=False,
    )
    assert not pytorch.enable_sagemaker_metrics


def test_pt_add_environment_variables(
    sagemaker_session, pytorch_training_version, pytorch_training_py_version
):
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_training_version,
        py_version=pytorch_training_py_version,
        environment=ENV_INPUT,
    )
    assert pytorch.environment


def test_pt_miss_environment_variables(
    sagemaker_session, pytorch_training_version, pytorch_training_py_version
):
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_training_version,
        py_version=pytorch_training_py_version,
        environment=None,
    )
    assert not pytorch.environment


def test_pt_default_sm_metrics(
    sagemaker_session, pytorch_training_version, pytorch_training_py_version
):
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_training_version,
        py_version=pytorch_training_py_version,
    )
    if Version(pytorch_training_version) < Version("1.3"):
        assert pytorch.enable_sagemaker_metrics is None
    else:
        assert pytorch.enable_sagemaker_metrics


def test_custom_image_estimator_deploy(
    sagemaker_session, pytorch_inference_version, pytorch_inference_py_version
):
    custom_image = "mycustomimage:latest"
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
    )
    pytorch.fit(inputs="s3://mybucket/train", job_name="new_name")
    model = pytorch.create_model(image_uri=custom_image)
    assert model.image_uri == custom_image


def test_pt_heterogeneous_cluster_distribution_config(
    sagemaker_session, pytorch_training_version, pytorch_training_py_version
):
    training_group = InstanceGroup("train_group", "ml.c4.xlarge", 1)
    expected_return = {"mpi": {"enabled": True}, "instance_groups": ["train_group"]}
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_training_version,
        py_version=pytorch_training_py_version,
        instance_groups=[training_group],
        distribution={
            "mpi": {"enabled": True},
            "instance_groups": [training_group],
        },
    )
    assert pytorch.distribution == expected_return


@patch("sagemaker.utils.repack_model", MagicMock())
@patch("sagemaker.utils.create_tar_file", MagicMock())
def test_register_pytorch_model_auto_infer_framework(
    sagemaker_session, pytorch_inference_version, pytorch_inference_py_version
):

    model_package_group_name = "test-pytorch-register-model"
    content_types = ["application/json"]
    response_types = ["application/json"]
    inference_instances = ["ml.m4.xlarge"]
    transform_instances = ["ml.m4.xlarge"]
    image_uri = "fakeimage"

    pytorch_model = PyTorchModel(
        MODEL_DATA,
        role=ROLE,
        entry_point=SCRIPT_PATH,
        framework_version=pytorch_inference_version,
        py_version=pytorch_inference_py_version,
        sagemaker_session=sagemaker_session,
    )

    pytorch_model.register(
        content_types,
        response_types,
        inference_instances,
        transform_instances,
        model_package_group_name=model_package_group_name,
        marketplace_cert=True,
        image_uri=image_uri,
    )

    expected_create_model_package_request = {
        "containers": [
            {
                "Image": image_uri,
                "Environment": ANY,
                "ModelDataUrl": ANY,
                "Framework": "PYTORCH",
                "FrameworkVersion": pytorch_inference_version,
            },
        ],
        "content_types": content_types,
        "response_types": response_types,
        "inference_instances": inference_instances,
        "transform_instances": transform_instances,
        "model_package_group_name": model_package_group_name,
        "marketplace_cert": True,
    }
    sagemaker_session.create_model_package_from_containers.assert_called_with(
        **expected_create_model_package_request
    )


def test_pytorch_ddp_distribution_configuration(
    sagemaker_session, pytorch_ddp_framework_version, pytorch_ddp_py_version
):
    test_instance_type = "ml.p4d.24xlarge"
    pytorch = _pytorch_estimator(
        sagemaker_session,
        framework_version=pytorch_ddp_framework_version,
        py_version=pytorch_ddp_py_version,
        distribution=DISTRIBUTION_PYTORCH_DDP_ENABLED,
        instance_type=test_instance_type,
    )
    actual_pytorch_ddp = pytorch._pytorch_distribution_configuration(
        distribution=pytorch.distribution
    )
    expected_torch_ddp = {
        "sagemaker_distributed_dataparallel_enabled": True,
        "sagemaker_distributed_dataparallel_custom_mpi_options": "",
        "sagemaker_instance_type": test_instance_type,
    }
    assert actual_pytorch_ddp == expected_torch_ddp


def test_pytorch_ddp_distribution_configuration_unsupported(sagemaker_session):
    unsupported_framework_version = "1.5.0"
    unsupported_py_version = "py2"
    with pytest.raises(ValueError) as error:
        _pytorch_estimator(
            sagemaker_session,
            framework_version=unsupported_framework_version,
            py_version=unsupported_py_version,
            distribution=DISTRIBUTION_PYTORCH_DDP_ENABLED,
        )
    assert (f"framework_version {unsupported_framework_version} is not supported") in str(error)
    assert (f"py_version {unsupported_py_version} is not supported") in str(error)


def test_predictor_with_component_name(sagemaker_session, component_name):
    predictor = PyTorchPredictor("endpoint", sagemaker_session, component_name=component_name)

    assert predictor._get_component_name() == component_name


def test_training_recipe_for_cpu(sagemaker_session):
    container_log_level = '"logging.INFO"'

    recipe_overrides = {
        "run": {
            "results_dir": "/opt/ml/model",
        },
        "exp_manager": {
            "explicit_log_dir": "/opt/ml/output/tensorboard",
            "checkpoint_dir": "/opt/ml/checkpoints",
        },
        "model": {
            "data": {
                "train_dir": "/opt/ml/input/data/train",
                "val_dir": "/opt/ml/input/data/val",
            },
        },
    }

    with pytest.raises(ValueError):
        PyTorch(
            output_path="s3://output_path",
            role=ROLE,
            sagemaker_session=sagemaker_session,
            instance_count=INSTANCE_COUNT,
            instance_type=INSTANCE_TYPE,
            base_job_name="job",
            container_log_level=container_log_level,
            training_recipe="training/llama/hf_llama3_8b_seq8k_gpu_p5x16_pretrain",
            recipe_overrides=recipe_overrides,
        )


@pytest.mark.parametrize(
    "recipe, model",
    [
        ("hf_llama3_8b_seq8k_gpu_p5x16_pretrain", "llama"),
        ("hf_mistral_7b_seq8k_gpu_p5x16_pretrain", "mistral"),
        ("hf_mixtral_8x7b_seq8k_gpu_p5x16_pretrain", "mixtral"),
    ],
)
def test_training_recipe_for_gpu(sagemaker_session, recipe, model):
    container_log_level = '"logging.INFO"'

    recipe_overrides = {
        "run": {
            "results_dir": "/opt/ml/model",
        },
        "exp_manager": {
            "explicit_log_dir": "/opt/ml/output",
            "checkpoint_dir": "/opt/ml/checkpoints",
        },
        "model": {
            "data": {
                "train_dir": "/opt/ml/input/data/train",
                "val_dir": "/opt/ml/input/data/val",
            },
        },
    }
    pytorch = PyTorch(
        output_path="s3://output_path",
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE_GPU,
        base_job_name="job",
        container_log_level=container_log_level,
        training_recipe=f"training/{model}/{recipe}",
        recipe_overrides=recipe_overrides,
    )

    assert pytorch.source_dir == "."
    assert pytorch.entry_point == f"{model}_pretrain.py"
    expected_distribution = {
        "torch_distributed": {
            "enabled": True,
        },
        "smdistributed": {
            "modelparallel": {
                "enabled": True,
                "parameters": {
                    "placement_strategy": "cluster",
                },
            },
        },
    }
    assert pytorch.distribution.items() == expected_distribution.items()


def test_training_recipe_with_override(sagemaker_session):
    container_log_level = '"logging.INFO"'

    recipe_overrides = {
        "run": {
            "results_dir": "/opt/ml/model",
        },
        "exp_manager": {
            "explicit_log_dir": "/opt/ml/output",
            "checkpoint_dir": "/opt/ml/checkpoints",
        },
        "model": {
            "data": {
                "train_dir": "/opt/ml/input/data/train",
                "val_dir": "/opt/ml/input/data/val",
            },
            "model_type": "mistral",
        },
    }
    pytorch = PyTorch(
        output_path="s3://output_path",
        role=ROLE,
        image_uri=IMAGE_URI,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE_GPU,
        base_job_name="job",
        container_log_level=container_log_level,
        training_recipe="training/llama/hf_llama3_8b_seq8k_gpu_p5x16_pretrain",
        recipe_overrides=recipe_overrides,
    )

    assert pytorch.source_dir == "."
    assert pytorch.entry_point == "mistral_pretrain.py"
    assert pytorch.image_uri == IMAGE_URI


def test_training_recipe_gpu_custom_source_dir(sagemaker_session):
    container_log_level = '"logging.INFO"'

    recipe_overrides = {
        "run": {
            "results_dir": "/opt/ml/model",
        },
        "exp_manager": {
            "explicit_log_dir": "/opt/ml/output",
            "checkpoint_dir": "/opt/ml/checkpoints",
        },
        "model": {
            "data": {
                "train_dir": "/opt/ml/input/data/train",
                "val_dir": "/opt/ml/input/data/val",
            },
            "model_type": "mistral",
        },
    }
    source_dir = tempfile.TemporaryDirectory(prefix="source_")
    pytorch = PyTorch(
        output_path="s3://output_path",
        role=ROLE,
        image_uri=IMAGE_URI,
        source_dir=source_dir.name,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE_GPU,
        base_job_name="job",
        container_log_level=container_log_level,
        training_recipe="training/llama/hf_llama3_8b_seq8k_gpu_p5x16_pretrain",
        recipe_overrides=recipe_overrides,
    )

    assert pytorch.source_dir == source_dir.name
    assert pytorch.entry_point == "mistral_pretrain.py"
    assert pytorch.image_uri == IMAGE_URI


def test_training_recipe_for_trainium(sagemaker_session):
    container_log_level = '"logging.INFO"'

    recipe_overrides = {
        "run": {
            "results_dir": "/opt/ml/model",
        },
        "exp_manager": {
            "explicit_log_dir": "/opt/ml/output",
        },
        "data": {
            "train_dir": "/opt/ml/input/data/train",
        },
        "model": {
            "model_config": "/opt/ml/input/data/train/config.json",
        },
        "compiler_cache_url": "s3://s3://output_path/neuron-cache",
    }
    pytorch = PyTorch(
        output_path="s3://output_path",
        role=ROLE,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE_TRAINIUM,
        base_job_name="job",
        container_log_level=container_log_level,
        training_recipe=NEURON_RECIPE,
        recipe_overrides=recipe_overrides,
    )

    assert pytorch.source_dir == "."
    assert pytorch.entry_point == "training_orchestrator.py"
    expected_distribution = {
        "torch_distributed": {
            "enabled": True,
        },
    }
    assert pytorch.distribution == expected_distribution


@pytest.mark.parametrize(
    "test_case",
    [
        {
            "script": "llama_pretrain.py",
            "recipe": {
                "model": {
                    "model_type": "llama_v3",
                },
            },
        },
        {
            "script": "mistral_pretrain.py",
            "recipe": {
                "model": {
                    "model_type": "mistral",
                },
            },
        },
        {
            "script": "deepseek_pretrain.py",
            "recipe": {
                "model": {
                    "model_type": "deepseek_llamav3",
                },
            },
        },
        {
            "script": "deepseek_pretrain.py",
            "recipe": {
                "model": {
                    "model_type": "deepseek_qwenv2",
                },
            },
        },
    ],
)
@patch("shutil.copyfile")
def test_get_training_recipe_gpu_script(mock_copyfile, test_case):
    script = test_case["script"]
    recipe = test_case["recipe"]
    mock_copyfile.return_value = None

    assert _get_training_recipe_gpu_script("code_dir", recipe, "source_dir") == script


def test_training_recipe_for_trainium_custom_source_dir(sagemaker_session):
    container_log_level = '"logging.INFO"'

    recipe_overrides = {
        "run": {
            "results_dir": "/opt/ml/model",
        },
        "exp_manager": {
            "explicit_log_dir": "/opt/ml/output",
        },
        "data": {
            "train_dir": "/opt/ml/input/data/train",
        },
        "model": {
            "model_config": "/opt/ml/input/data/train/config.json",
        },
        "compiler_cache_url": "s3://s3://output_path/neuron-cache",
    }
    source_dir = tempfile.TemporaryDirectory(prefix="source_")
    pytorch = PyTorch(
        output_path="s3://output_path",
        role=ROLE,
        source_dir=source_dir.name,
        sagemaker_session=sagemaker_session,
        instance_count=INSTANCE_COUNT,
        instance_type=INSTANCE_TYPE_TRAINIUM,
        base_job_name="job",
        container_log_level=container_log_level,
        training_recipe=NEURON_RECIPE,
        recipe_overrides=recipe_overrides,
    )

    assert pytorch.source_dir == source_dir.name
    assert pytorch.entry_point == "training_orchestrator.py"
    expected_distribution = {
        "torch_distributed": {
            "enabled": True,
        },
    }
    assert pytorch.distribution == expected_distribution


def test_training_recipe_images_uri():
    gpu_image_cfg = {"framework": "pytorch-smp", "version": "2.4.1", "additional_args": {}}
    gpu_image_uri = _get_training_recipe_image_uri(gpu_image_cfg, "us-west-2")
    assert gpu_image_uri == RECIPE_GPU_IMAGE
    neuron_image_cfg = {
        "framework": "hyperpod-recipes-neuron",
        "version": "2.1.2",
        "additional_args": {},
    }
    neuron_image_uri = _get_training_recipe_image_uri(neuron_image_cfg, "us-west-2")
    assert neuron_image_uri == RECIPE_NEURON_IMAGE
