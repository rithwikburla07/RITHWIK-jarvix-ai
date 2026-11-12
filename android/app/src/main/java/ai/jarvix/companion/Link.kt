package ai.jarvix.companion

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.Proxy
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

const val MAX_FILE = 2_000_000_000L
private const val BUFFER = 65536
private val pinPattern = Regex("[a-f0-9]{64}")
private val tokenPattern = Regex("[A-Za-z0-9_-]{24,80}")

fun privateAddress(value: String): Boolean {
    val parts = value.split(":")
    if (parts.size != 2) return false
    val port = parts[1].toIntOrNull() ?: return false
    if (port !in 1024..65535) return false
    val bytes = parts[0].split(".").map { part ->
        if (!Regex("0|[1-9][0-9]{0,2}").matches(part)) return false
        part.toInt()
    }
    if (bytes.size != 4 || bytes.any { it !in 0..255 }) return false
    return bytes[0] == 10 || bytes[0] == 172 && bytes[1] in 16..31 || bytes[0] == 192 && bytes[1] == 168
}

fun validateFile(name: String, size: Long, hash: String) {
    require(name.length in 1..240 && name !in setOf(".", "..") &&
        name.none { it == '/' || it == '\\' || it.isISOControl() })
    require(size in 0..MAX_FILE && pinPattern.matches(hash))
}

data class Identity(val address: String, val pin: String, val device: String = "", val token: String = "") {
    init { require(privateAddress(address) && pinPattern.matches(pin)) }
    fun json() = JSONObject().put("address", address).put("pin", pin).put("device", device).put("token", token)
    companion object {
        fun parse(json: JSONObject): Identity = Identity(json.getString("address"), json.getString("pin"),
            json.optString("device"), json.optString("token"))
    }
}

class CredentialStore(context: Context) {
    private val prefs = context.getSharedPreferences("jarvix_private", Context.MODE_PRIVATE)
    private val alias = "JarvixCompanionCredentials"
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        return (store.getKey(alias, null) as? SecretKey) ?: KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore"
        ).apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun load(): Identity? {
        val encrypted = prefs.getString("connection", null) ?: return null
        return try {
            val bytes = Base64.decode(encrypted, Base64.NO_WRAP)
            require(bytes.size > 12)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(0, 12)))
            Identity.parse(JSONObject(String(cipher.doFinal(bytes.copyOfRange(12, bytes.size)), Charsets.UTF_8)))
        } catch (_: Exception) { null }
    }
    fun save(identity: Identity) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val bytes = cipher.iv + cipher.doFinal(identity.json().toString().toByteArray(Charsets.UTF_8))
        check(prefs.edit().putString("connection", Base64.encodeToString(bytes, Base64.NO_WRAP)).commit())
    }
    fun clear() { prefs.edit().remove("connection").apply() }
}

private fun bytesFromHex(hex: String) = hex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
private fun shaHex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }

class Link(private val identity: Identity) {
    private val expected = bytesFromHex(identity.pin)
    private val tls = SSLContext.getInstance("TLS").apply {
        init(null, arrayOf(object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<X509Certificate>, type: String) {
                throw CertificateException("Client certificate authentication is unsupported.")
            }
            override fun checkServerTrusted(chain: Array<X509Certificate>, type: String) {
                val certificate = chain.firstOrNull() ?: throw CertificateException("Missing Jarvix certificate.")
                certificate.checkValidity()
                val actual = MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
                if (!MessageDigest.isEqual(actual, expected)) throw CertificateException("Jarvix certificate changed.")
            }
        }), null)
    }
    private var session = ""

    private fun <T> request(path: String, body: JSONObject, read: (HttpsURLConnection) -> T): T {
        val connection = URL("https://" + identity.address + "/v1/" + path)
            .openConnection(Proxy.NO_PROXY) as HttpsURLConnection
        try {
            connection.sslSocketFactory = tls.socketFactory
            connection.hostnameVerifier = javax.net.ssl.HostnameVerifier { host, _ ->
                host == identity.address.substringBefore(":")
            }
            connection.connectTimeout = 4500
            connection.readTimeout = 7000
            connection.instanceFollowRedirects = false
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            val bytes = body.toString().toByteArray(Charsets.UTF_8)
            require(bytes.size <= 4096)
            connection.setFixedLengthStreamingMode(bytes.size)
            connection.outputStream.use { it.write(bytes) }
            if (connection.responseCode != 200) {
                throw IOException("Jarvix returned HTTP " + connection.responseCode + ".")
            }
            return read(connection)
        } finally { connection.disconnect() }
    }

    private fun json(path: String, body: JSONObject): JSONObject = request(path, body) { connection ->
        connection.inputStream.use { input ->
            val result = java.io.ByteArrayOutputStream()
            val bytes = ByteArray(4096)
            while (true) {
                val count = input.read(bytes)
                if (count < 0) break
                if (result.size() + count > 32768) throw IOException("Jarvix response is too large.")
                result.write(bytes, 0, count)
            }
            JSONObject(result.toString("UTF-8"))
        }
    }

    private fun signed(args: JSONObject): JSONObject {
        if (session.isEmpty()) session = json("session", JSONObject().put("hello", true)).getString("session")
        require(tokenPattern.matches(identity.token))
        return args.put("device", identity.device).put("token", identity.token).put("session", session)
            .put("nonce", UUID.randomUUID().toString().replace("-", ""))
            .put("time", System.currentTimeMillis() / 1000)
    }

    fun pair(invite: String, label: String): Identity {
        require(tokenPattern.matches(invite))
        val reply = json("pair", JSONObject().put("invite", invite).put("label", label))
        return identity.copy(device = reply.getString("device"), token = reply.getString("token"))
    }

    fun poll() = try { json("poll", signed(JSONObject())).getJSONArray("jobs") }
    catch (problem: IOException) { session = ""; throw problem }

    fun accept(id: String) { json("accept", signed(JSONObject().put("id", id))) }
    fun cancel(id: String) { json("cancel", signed(JSONObject().put("id", id))) }

    fun finish(id: String, ok: Boolean, hash: String = "") {
        json("finish", signed(JSONObject().put("id", id).put("ok", ok).put("sha256", hash)))
    }

    fun download(id: String, name: String, size: Long, hash: String, folder: File, stop: AtomicBoolean,
                 progress: (Long) -> Unit): File {
        validateFile(name, size, hash)
        if (folder.usableSpace < size + 16_000_000) throw IOException("Free more space to receive this file.")
        val file = File.createTempFile("jarvix-transfer-", ".part", folder)
        try {
            request("download", signed(JSONObject().put("id", id))) { connection ->
                if (connection.getHeaderFieldLong("Content-Length", -1) != size)
                    throw IOException("Jarvix reported an unexpected file size.")
                val digest = MessageDigest.getInstance("SHA-256")
                var written = 0L
                val bytes = ByteArray(BUFFER)
                connection.inputStream.use { input -> file.outputStream().buffered(BUFFER).use { output ->
                    while (!stop.get()) {
                        val count = input.read(bytes)
                        if (count < 0) break
                        written += count
                        if (written > size) throw IOException("Transfer exceeded its declared size.")
                        output.write(bytes, 0, count)
                        digest.update(bytes, 0, count)
                        progress(written)
                    }
                } }
                if (stop.get() || written != size || !MessageDigest.isEqual(digest.digest(), bytesFromHex(hash)))
                    throw IOException("Transfer cancelled, incomplete or corrupt.")
            }
            return file
        } catch (problem: Exception) {
            file.delete()
            throw problem
        }
    }
}
