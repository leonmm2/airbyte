/*
 * Copyright (c) 2025 Airbyte, Inc., all rights reserved.
 */

package io.airbyte.cdk.load.task.internal

import io.airbyte.cdk.load.command.DestinationConfiguration
import io.airbyte.cdk.load.message.DestinationRecordRaw
import io.airbyte.cdk.load.message.PipelineHeartbeat
import io.airbyte.cdk.load.message.StreamKey
import io.airbyte.cdk.load.task.OnEndOfSync
import io.airbyte.cdk.load.task.Task
import io.airbyte.cdk.load.task.TerminalCondition
import kotlinx.coroutines.channels.ClosedSendChannelException
import kotlinx.coroutines.delay

class HeartbeatTask(
    private val config: DestinationConfiguration,
    private val heartbeatAction:
        suspend (PipelineHeartbeat<StreamKey, DestinationRecordRaw>) -> Unit
) : Task {
    override val terminalCondition: TerminalCondition = OnEndOfSync

    override suspend fun execute() {
        while (true) {
            delay(config.heartbeatIntervalSeconds * 1000L)
            try {
                heartbeatAction(PipelineHeartbeat())
            } catch (e: ClosedSendChannelException) {
                // Do nothing. We don't care. Move on
            }
        }
    }
}
