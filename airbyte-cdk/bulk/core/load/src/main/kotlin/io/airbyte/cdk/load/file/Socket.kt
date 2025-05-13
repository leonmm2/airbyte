package io.airbyte.cdk.load.file

import io.github.oshai.kotlinlogging.KotlinLogging
import java.io.File
import java.io.InputStream
import java.net.StandardProtocolFamily
import java.net.UnixDomainSocketAddress
import java.nio.channels.Channels
import java.nio.channels.SocketChannel
import java.nio.file.Files
import java.nio.file.attribute.PosixFilePermissions
import kotlinx.coroutines.delay

class Socket(
    private val socketPath: String,
    private val bufferSizeBytes: Int,
    private val connectWaitDelayMs: Long = 1000L,
    private val setPermissions: Boolean = false
) {
    private val log = KotlinLogging.logger {}

    suspend fun connect(block: suspend (InputStream) -> Unit) {
        val socketFile = File(socketPath)
        while (!socketFile.exists()) {
            log.info { "Waiting for socket file to be created: $socketPath" }
            delay(connectWaitDelayMs)
        }

        // TODO: This should be done for us by the platform
        if (setPermissions) {
            Files.setPosixFilePermissions(
                socketFile.toPath(),
                PosixFilePermissions.fromString("rwxrwxrwx")
            )
        }

        val address = UnixDomainSocketAddress.of(socketFile.toPath())
        SocketChannel.open(StandardProtocolFamily.UNIX).use { channel ->
            log.info { "Socket $socketPath opened" }

            if (!channel.connect(address)) {
                throw IllegalStateException("Failed to connect to socket $socketPath")
            }

            log.info { "Socket $socketPath connected for reading" }


            Channels.newInputStream(channel).buffered(bufferSizeBytes).use { inputStream ->
                block(inputStream)
            }
        }
    }
}
