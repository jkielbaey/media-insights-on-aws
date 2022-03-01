# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

###############################################################################
# PURPOSE:
#   This operator uses Mediaconvert to do three things:
#     1) create a thumbnail for a video
#     2) extract audio track from video
#     3) transcode the video into an MP4 format supported by Rekognition
#   For thumbnails, it will grab the frame from 7 seconds into the video.
#   That position can be configured with the "ThumbnailPosition" argument.
#   The transcode video is called a "proxy encode" and is used by Rekognition
#   operators instead of the original video uploaded by a user.
#
# OUTPUT:
#   Thumbnails and transcoded video will be saved to the following path:
#       s3://" + $DATAPLANE_BUCKET + "/" + 'private/assets/' + asset_id + "/"
#   The thumbnail filename will end with "_thumbnail.0000001.jpg".
#   If the user specifies a thumbnail position that exceeds the video duration
#   then a thumbnail will be created at time 0 and have a filename ending
#   with end with "_thumbnail.0000000.jpg".
#
#   Thumbnail position can be controlled in the workflow configuration, like this:
#   '"Thumbnail":{"Position":7, "Enabled":true}'
#
###############################################################################

import os
import boto3
import json
from botocore import config
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from MediaInsightsEngineLambdaHelper import MediaInsightsOperationHelper
from MediaInsightsEngineLambdaHelper import MasExecutionError

patch_all()

region = os.environ['AWS_REGION']
mie_config = json.loads(os.environ['botoConfig'])
config = config.Config(**mie_config)

mediaconvert_role = os.environ['mediaconvertRole']
dataplane_bucket = os.environ['DATAPLANE_BUCKET']
mediaconvert = boto3.client("mediaconvert", config=config, region_name=region)


def lambda_handler(event, context):
    print("We got the following event:\n", event)
    operator_object = MediaInsightsOperationHelper(event)

    try:
        workflow_id = str(operator_object.workflow_execution_id)
        input_bucket = operator_object.input["Media"]["Video"]["S3Bucket"]
        input_key = operator_object.input["Media"]["Video"]["S3Key"]
    except KeyError as e:
        operator_object.update_workflow_status("Error")
        operator_object.add_workflow_metadata(ThumbnailError="Missing a required metadata key {e}".format(e=e))
        raise MasExecutionError(operator_object.return_output_object())

    # Adding in exception block for now since we aren't guaranteed an asset id will be present, should remove later
    try:
        asset_id = operator_object.asset_id
    except KeyError as e:
        print("No asset id passed in with this workflow", e)
        asset_id = ''
    file_input = "s3://" + input_bucket + "/" + input_key
    audio_destination = "s3://" + dataplane_bucket + "/" + 'private/assets/' + asset_id + "/workflows/" + workflow_id + "/"
    thumbnail_destination = "s3://" + dataplane_bucket + "/" + 'private/assets/' + asset_id + "/"
    proxy_destination = "s3://" + dataplane_bucket + "/" + 'private/assets/' + asset_id + "/"

    # Get user-defined location for generic data file
    if "ThumbnailPosition" in operator_object.configuration:
        thumbnail_position = int(operator_object.configuration["ThumbnailPosition"])
    else:
        thumbnail_position = 7

    mediaconvert_endpoint = os.environ["MEDIACONVERT_ENDPOINT"]
    customer_mediaconvert = boto3.client("mediaconvert", region_name=region, endpoint_url=mediaconvert_endpoint)
    
    try:
        response = customer_mediaconvert.create_job(
            Role=mediaconvert_role,
            Settings={
                "OutputGroups": [
                    {
                        "CustomName": "thumbnail",
                        "Name": "File Group",
                        "Outputs": [
                            {
                                "ContainerSettings": {
                                    "Container": "RAW"
                                },
                                "VideoDescription": {
                                    "ScalingBehavior": "DEFAULT",
                                    "TimecodeInsertion": "DISABLED",
                                    "AntiAlias": "ENABLED",
                                    "Sharpness": 50,
                                    "CodecSettings": {
                                        "Codec": "FRAME_CAPTURE",
                                        "FrameCaptureSettings": {
                                            "FramerateNumerator": 1,
                                            "FramerateDenominator": thumbnail_position,
                                            "MaxCaptures": 2,
                                            "Quality": 80
                                        }
                                    },
                                    "DropFrameTimecode": "ENABLED",
                                    "ColorMetadata": "INSERT"
                                },
                                "Extension": "jpg",
                                "NameModifier": "_thumbnail"
                            }
                        ],
                        "OutputGroupSettings": {
                            "Type": "FILE_GROUP_SETTINGS",
                            "FileGroupSettings": {
                                "Destination": thumbnail_destination
                            }
                        }
                    },
                    {
                        "Name": "File Group",
                        "Outputs": [{
                            "ContainerSettings": {
                                "Container": "MP4",
                                "Mp4Settings": {
                                    "CslgAtom": "INCLUDE",
                                    "FreeSpaceBox": "EXCLUDE",
                                    "MoovPlacement": "PROGRESSIVE_DOWNLOAD"
                                }
                            },
                            "AudioDescriptions": [{
                                "AudioTypeControl": "FOLLOW_INPUT",
                                "AudioSourceName": "Audio Selector 1",
                                "CodecSettings": {
                                    "Codec": "AAC",
                                    "AacSettings": {
                                        "AudioDescriptionBroadcasterMix": "NORMAL",
                                        "Bitrate": 96000,
                                        "RateControlMode": "CBR",
                                        "CodecProfile": "LC",
                                        "CodingMode": "CODING_MODE_2_0",
                                        "RawFormat": "NONE",
                                        "SampleRate": 48000,
                                        "Specification": "MPEG4"
                                    }
                                },
                                "LanguageCodeControl": "FOLLOW_INPUT"
                            }],
                            "Extension": "mp4",
                            "NameModifier": "_audio"
                        }],
                        "OutputGroupSettings": {
                            "Type": "FILE_GROUP_SETTINGS",
                            "FileGroupSettings": {
                                "Destination": audio_destination
                            }
                        }
                    },
                    {
                        "CustomName": "proxy",
                        "Name": "File Group",
                        "Outputs": [
                            {
                                "VideoDescription": {
                                    "ScalingBehavior": "DEFAULT",
                                    "TimecodeInsertion": "DISABLED",
                                    "AntiAlias": "ENABLED",
                                    "Sharpness": 50,
                                    "CodecSettings": {
                                        "Codec": "H_264",
                                        "H264Settings": {
                                            "InterlaceMode": "PROGRESSIVE",
                                            "NumberReferenceFrames": 3,
                                            "Syntax": "DEFAULT",
                                            "Softness": 0,
                                            "GopClosedCadence": 1,
                                            "GopSize": 90,
                                            "Slices": 1,
                                            "GopBReference": "DISABLED",
                                            "SlowPal": "DISABLED",
                                            "SpatialAdaptiveQuantization": "ENABLED",
                                            "TemporalAdaptiveQuantization": "ENABLED",
                                            "FlickerAdaptiveQuantization": "DISABLED",
                                            "EntropyEncoding": "CABAC",
                                            "Bitrate": 1600000,
                                            "FramerateControl": "SPECIFIED",
                                            "RateControlMode": "CBR",
                                            "CodecProfile": "MAIN",
                                            "Telecine": "NONE",
                                            "MinIInterval": 0,
                                            "AdaptiveQuantization": "HIGH",
                                            "CodecLevel": "AUTO",
                                            "FieldEncoding": "PAFF",
                                            "SceneChangeDetect": "ENABLED",
                                            "QualityTuningLevel": "SINGLE_PASS",
                                            "FramerateConversionAlgorithm": "DUPLICATE_DROP",
                                            "UnregisteredSeiTimecode": "DISABLED",
                                            "GopSizeUnits": "FRAMES",
                                            "ParControl": "SPECIFIED",
                                            "NumberBFramesBetweenReferenceFrames": 2,
                                            "RepeatPps": "DISABLED",
                                            "FramerateNumerator": 30,
                                            "FramerateDenominator": 1,
                                            "ParNumerator": 1,
                                            "ParDenominator": 1
                                        }
                                    },
                                    "AfdSignaling": "NONE",
                                    "DropFrameTimecode": "ENABLED",
                                    "RespondToAfd": "NONE",
                                    "ColorMetadata": "INSERT"
                                },
                                "AudioDescriptions": [
                                    {
                                        "AudioTypeControl": "FOLLOW_INPUT",
                                        "CodecSettings": {
                                            "Codec": "AAC",
                                            "AacSettings": {
                                                "AudioDescriptionBroadcasterMix": "NORMAL",
                                                "RateControlMode": "CBR",
                                                "CodecProfile": "LC",
                                                "CodingMode": "CODING_MODE_2_0",
                                                "RawFormat": "NONE",
                                                "SampleRate": 48000,
                                                "Specification": "MPEG4",
                                                "Bitrate": 64000
                                            }
                                        },
                                        "LanguageCodeControl": "FOLLOW_INPUT",
                                        "AudioSourceName": "Audio Selector 1"
                                    }
                                ],
                                "ContainerSettings": {
                                    "Container": "MP4",
                                    "Mp4Settings": {
                                        "CslgAtom": "INCLUDE",
                                        "FreeSpaceBox": "EXCLUDE",
                                        "MoovPlacement": "PROGRESSIVE_DOWNLOAD"
                                    }
                                },
                                "Extension": "mp4",
                                "NameModifier": "_proxy"
                            }
                        ],
                        "OutputGroupSettings": {
                            "Type": "FILE_GROUP_SETTINGS",
                            "FileGroupSettings": {
                                "Destination": proxy_destination
                            }
                        }
                    },
                    {
                        "Name": "Apple HLS",
                        "Outputs": [
                            {
                                "ContainerSettings": {
                                    "Container": "M3U8",
                                    "M3u8Settings": {}
                                },
                                "VideoDescription": {
                                    "Width": 1920,
                                    "Height": 1080,
                                    "CodecSettings": {
                                        "Codec": "H_264",
                                        "H264Settings": {
                                            "MaxBitrate": 4000000,
                                            "RateControlMode": "QVBR",
                                            "SceneChangeDetect": "TRANSITION_DETECTION"
                                        }
                                    }
                                },
                                "AudioDescriptions": [
                                    {
                                        "CodecSettings": {
                                            "Codec": "AAC",
                                            "AacSettings": {
                                                "Bitrate": 96000,
                                                "CodingMode": "CODING_MODE_2_0",
                                                "SampleRate": 48000
                                            }
                                        }
                                    }
                                ],
                                "OutputSettings": {
                                    "HlsSettings": {}
                                },
                                "NameModifier": "_1080p"
                            },
                            {
                                "ContainerSettings": {
                                    "Container": "M3U8",
                                    "M3u8Settings": {}
                                },
                                "VideoDescription": {
                                    "Width": 1280,
                                    "Height": 720,
                                    "CodecSettings": {
                                        "Codec": "H_264",
                                        "H264Settings": {
                                            "MaxBitrate": 2500000,
                                            "RateControlMode": "QVBR",
                                            "SceneChangeDetect": "TRANSITION_DETECTION"
                                        }
                                    }
                                },
                                "AudioDescriptions": [
                                    {
                                        "CodecSettings": {
                                            "Codec": "AAC",
                                            "AacSettings": {
                                                "Bitrate": 96000,
                                                "CodingMode": "CODING_MODE_2_0",
                                                "SampleRate": 48000
                                            }
                                        }
                                    }
                                ],
                                "OutputSettings": {
                                    "HlsSettings": {}
                                },
                                "NameModifier": "_720ph"
                            },
                            {
                                "ContainerSettings": {
                                    "Container": "M3U8",
                                    "M3u8Settings": {}
                                },
                                "VideoDescription": {
                                    "Width": 1280,
                                    "Height": 720,
                                    "CodecSettings": {
                                        "Codec": "H_264",
                                        "H264Settings": {
                                            "MaxBitrate": 1500000,
                                            "RateControlMode": "QVBR",
                                            "SceneChangeDetect": "TRANSITION_DETECTION"
                                        }
                                    }
                                },
                                "AudioDescriptions": [
                                    {
                                        "CodecSettings": {
                                            "Codec": "AAC",
                                            "AacSettings": {
                                                "Bitrate": 96000,
                                                "CodingMode": "CODING_MODE_2_0",
                                                "SampleRate": 48000
                                            }
                                        }
                                    }
                                ],
                                "OutputSettings": {
                                    "HlsSettings": {}
                                },
                                "NameModifier": "_720pl"
                            },
                            {
                                "ContainerSettings": {
                                    "Container": "M3U8",
                                    "M3u8Settings": {}
                                },
                                "VideoDescription": {
                                    "Width": 960,
                                    "Height": 540,
                                    "CodecSettings": {
                                        "Codec": "H_264",
                                        "H264Settings": {
                                            "MaxBitrate": 1000000,
                                            "RateControlMode": "QVBR",
                                            "SceneChangeDetect": "TRANSITION_DETECTION"
                                        }
                                    }
                                },
                                "AudioDescriptions": [
                                    {
                                        "CodecSettings": {
                                            "Codec": "AAC",
                                            "AacSettings": {
                                                "Bitrate": 96000,
                                                "CodingMode": "CODING_MODE_2_0",
                                                "SampleRate": 48000
                                            }
                                        }
                                    }
                                ],
                                "OutputSettings": {
                                    "HlsSettings": {}
                                },
                                "NameModifier": "_540p"
                            },
                            {
                                "ContainerSettings": {
                                    "Container": "M3U8",
                                    "M3u8Settings": {}
                                },
                                "VideoDescription": {
                                    "Width": 640,
                                    "Height": 360,
                                    "CodecSettings": {
                                        "Codec": "H_264",
                                        "H264Settings": {
                                            "MaxBitrate": 600000,
                                            "RateControlMode": "QVBR",
                                            "SceneChangeDetect": "TRANSITION_DETECTION"
                                        }
                                    }
                                },
                                "AudioDescriptions": [
                                    {
                                        "CodecSettings": {
                                            "Codec": "AAC",
                                            "AacSettings": {
                                                "Bitrate": 96000,
                                                "CodingMode": "CODING_MODE_2_0",
                                                "SampleRate": 48000
                                            }
                                        }
                                    }
                                ],
                                "OutputSettings": {
                                    "HlsSettings": {}
                                },
                                "NameModifier": "_360p"
                            }
                        ],
                        "OutputGroupSettings": {
                            "Type": "HLS_GROUP_SETTINGS",
                            "HlsGroupSettings": {
                                "SegmentLength": 6,
                                "Destination": proxy_destination,
                                "MinSegmentLength": 1,
                                "MinFinalSegmentLength": 1,
                                "SegmentControl": "SEGMENTED_FILES"
                            }
                        }
                    }
                    ],
                "Inputs": [{
                    "AudioSelectors": {
                        "Audio Selector 1": {
                            "Offset": 0,
                            "DefaultSelection": "DEFAULT",
                            "ProgramSelection": 1
                        }
                    },
                    "VideoSelector": {
                        "ColorSpace": "FOLLOW"
                    },
                    "FileInput": file_input
                }]
            }
        )

    # TODO: Add support for boto client error handling
    except Exception as e:
        print("Exception:\n", e)
        operator_object.update_workflow_status("Error")
        operator_object.add_workflow_metadata(ThumbnailError=str(e))
        raise MasExecutionError(operator_object.return_output_object())
    else:
        job_id = response['Job']['Id']
        operator_object.update_workflow_status("Executing")
        operator_object.add_workflow_metadata(MediaconvertJobId=job_id, MediaconvertInputFile=file_input, AssetId=asset_id, WorkflowExecutionId=workflow_id)
        return operator_object.return_output_object()
