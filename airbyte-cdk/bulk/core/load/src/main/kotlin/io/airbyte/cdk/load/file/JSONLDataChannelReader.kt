package io.airbyte.cdk.load.file

import io.airbyte.cdk.load.message.DestinationMessage
import io.airbyte.cdk.load.util.Jsons
import io.airbyte.protocol.models.v0.AirbyteMessage
import java.io.InputStream

class JSONLDataChannelReader(

): DataChannelReader {
    override fun read(inputStream: InputStream): Iterator<DestinationMessage> {
        Jsons
            .readerFor(AirbyteMessage::class.java)
            .readValues<AirbyteMessage>(inputStream)
            . {
                val destinationMessage = factory.fromAirbyteMessage(it, "")
                handleDestinationMessage(
                    destinationMessage,
                    streamRecordCounts,
                    collector,
                    count++
                )
            }
    }
}
