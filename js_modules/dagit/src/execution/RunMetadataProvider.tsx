import * as React from "react";
import produce from "immer";
import gql from "graphql-tag";

import { RunMetadataProviderMessageFragment } from "./types/RunMetadataProviderMessageFragment";

export enum IStepState {
  WAITING = "waiting",
  RUNNING = "running",
  SUCCEEDED = "succeeded",
  FAILED = "failed"
}

export interface IStepMetadata {
  state: IStepState;
  start?: number;
  elapsed?: number;
  transitionedAt: number;
}

export interface IRunMetadataDict {
  startingProcessAt?: number;
  startedProcessAt?: number;
  startedPipelineAt?: number;
  processId?: number;
  steps: {
    [stepName: string]: IStepMetadata;
  };
}

function extractMetadataFromLogs(
  logs: RunMetadataProviderMessageFragment[]
): IRunMetadataDict {
  const metadata: IRunMetadataDict = {
    steps: {}
  };

  logs.forEach(log => {
    if (log.__typename === "PipelineProcessStartEvent") {
      metadata.startingProcessAt = Number.parseInt(log.timestamp);
    }
    if (log.__typename === "PipelineProcessStartedEvent") {
      metadata.startedProcessAt = Number.parseInt(log.timestamp);
      metadata.processId = log.processId;
    }
    if (log.__typename === "PipelineStartEvent") {
      metadata.startedPipelineAt = Number.parseInt(log.timestamp);
    }

    if ("step" in log) {
      const name = log.step.name;
      const timestamp = Number.parseInt(log.timestamp, 10);

      if (log.__typename === "ExecutionStepStartEvent") {
        metadata.steps[name] = {
          state: IStepState.RUNNING,
          start: timestamp,
          transitionedAt: timestamp
        };
      } else if (log.__typename === "ExecutionStepSuccessEvent") {
        metadata.steps[name] = produce(metadata.steps[name] || {}, step => {
          step.state = IStepState.SUCCEEDED;
          if (step.start) {
            step.transitionedAt = timestamp;
            step.elapsed = timestamp - step.start;
          }
        });
      } else if (log.__typename === "ExecutionStepFailureEvent") {
        metadata.steps[name] = produce(metadata.steps[name] || {}, step => {
          step.state = IStepState.FAILED;
          if (step.start) {
            step.transitionedAt = timestamp;
            step.elapsed = timestamp - step.start;
          }
        });
      }
    }
  });
  return metadata;
}

interface IRunMetadataProviderProps {
  logs: RunMetadataProviderMessageFragment[];
  children: (metadata: IRunMetadataDict) => React.ReactElement<any>;
}

export default class RunMetadataProvider extends React.Component<
  IRunMetadataProviderProps
> {
  static fragments = {
    RunMetadataProviderMessageFragment: gql`
      fragment RunMetadataProviderMessageFragment on PipelineRunEvent {
        __typename
        ... on MessageEvent {
          message
          timestamp
        }
        ... on PipelineProcessStartedEvent {
          processId
        }
        ... on ExecutionStepEvent {
          step {
            name
          }
        }
      }
    `
  };

  render() {
    return this.props.children(extractMetadataFromLogs(this.props.logs));
  }
}
