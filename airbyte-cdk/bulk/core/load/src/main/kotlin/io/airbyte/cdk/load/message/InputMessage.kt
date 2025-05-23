/*
 * Copyright (c) 2024 Airbyte, Inc., all rights reserved.
 */

package io.airbyte.cdk.load.message

import com.fasterxml.jackson.databind.JsonNode
import io.airbyte.cdk.load.command.DestinationStream
import io.airbyte.cdk.load.data.AirbyteValue
import io.airbyte.cdk.load.data.json.JsonToAirbyteValue
import io.airbyte.cdk.load.data.json.toJson
import io.airbyte.cdk.load.message.CheckpointMessage.Checkpoint
import io.airbyte.cdk.load.message.CheckpointMessage.Stats
import io.airbyte.cdk.load.util.deserializeToNode
import io.airbyte.protocol.models.v0.AirbyteGlobalState
import io.airbyte.protocol.models.v0.AirbyteMessage
import io.airbyte.protocol.models.v0.AirbyteRecordMessage
import io.airbyte.protocol.models.v0.AirbyteRecordMessageFileReference
import io.airbyte.protocol.models.v0.AirbyteStateMessage

sealed interface InputMessage {
    fun asProtocolMessage(): AirbyteMessage
}

data class InputRecord(
    val stream: DestinationStream.Descriptor,
    val data: AirbyteValue,
    val emittedAtMs: Long,
    val meta: Meta?,
    val serialized: String,
    val fileReference: AirbyteRecordMessageFileReference? = null,
) : InputMessage {
    /** Convenience constructor, primarily intended for use in tests. */
    constructor(
        namespace: String?,
        name: String,
        data: String,
        emittedAtMs: Long,
        changes: MutableList<Meta.Change> = mutableListOf(),
        fileReference: AirbyteRecordMessageFileReference? = null,
    ) : this(
        stream = DestinationStream.Descriptor(namespace, name),
        data = JsonToAirbyteValue().convert(data.deserializeToNode()),
        emittedAtMs = emittedAtMs,
        meta = Meta(changes),
        serialized = "",
        fileReference,
    )

    override fun asProtocolMessage(): AirbyteMessage =
        AirbyteMessage()
            .withType(AirbyteMessage.Type.RECORD)
            .withRecord(
                AirbyteRecordMessage()
                    .withStream(stream.name)
                    .withNamespace(stream.namespace)
                    .withEmittedAt(emittedAtMs)
                    .withData(data.toJson())
                    .also {
                        if (meta != null) {
                            it.withMeta(meta.asProtocolObject())
                        }
                        if (fileReference != null) {
                            it.withFileReference(fileReference)
                        }
                    }
            )
}

data class InputFile(
    val file: DestinationFile,
) : InputMessage {
    constructor(
        stream: DestinationStream,
        emittedAtMs: Long,
        fileMessage: DestinationFile.AirbyteRecordMessageFile,
        serialized: String = ""
    ) : this(
        DestinationFile(
            stream,
            emittedAtMs,
            serialized,
            fileMessage,
        )
    )
    override fun asProtocolMessage(): AirbyteMessage = file.asProtocolMessage()
}

sealed interface InputCheckpoint : InputMessage

data class InputStreamCheckpoint(val checkpoint: StreamCheckpoint) : InputCheckpoint {
    constructor(
        streamNamespace: String?,
        streamName: String,
        blob: String,
        sourceRecordCount: Long,
        destinationRecordCount: Long? = null,
    ) : this(
        StreamCheckpoint(
            Checkpoint(
                DestinationStream.Descriptor(streamNamespace, streamName),
                state = blob.deserializeToNode()
            ),
            Stats(sourceRecordCount),
            destinationRecordCount?.let { Stats(it) },
            emptyMap(),
        )
    )
    override fun asProtocolMessage(): AirbyteMessage = checkpoint.asProtocolMessage()
}

data class InputGlobalCheckpoint(val sharedState: JsonNode?) : InputCheckpoint {
    override fun asProtocolMessage(): AirbyteMessage =
        AirbyteMessage()
            .withType(AirbyteMessage.Type.STATE)
            .withState(
                AirbyteStateMessage()
                    .withType(AirbyteStateMessage.AirbyteStateType.GLOBAL)
                    .withGlobal(AirbyteGlobalState().withSharedState(sharedState))
            )
}
