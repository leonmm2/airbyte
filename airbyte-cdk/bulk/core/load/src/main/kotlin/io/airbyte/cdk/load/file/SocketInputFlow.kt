package io.airbyte.cdk.load.file

import io.airbyte.cdk.load.config.PipelineInputEvent
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.FlowCollector

class SocketInputFlow(
    private val socket: Socket,
    private val inputFormatReader: DataChannelFormatReader,
): Flow<PipelineInputEvent> {
    override suspend fun collect(collector: FlowCollector<PipelineInputEvent>) {
        socket.connect { inputStream ->
            inputFormatReader.read(inputStream).forEach { message ->
                collector.emit(message)
            }
        }
    }
}
