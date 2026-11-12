package ai.jarvix.companion

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.media.Ringtone
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.AlarmClock
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import com.google.zxing.integration.android.IntentIntegrator
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class MainActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private val occupied = AtomicBoolean(false)
    private val cancelled = AtomicBoolean(false)
    private lateinit var store: CredentialStore
    private lateinit var status: TextView
    private lateinit var permissions: TextView
    private lateinit var invite: EditText
    private lateinit var offers: LinearLayout
    private lateinit var progress: ProgressBar
    private var link: Link? = null
    private var running = false
    private var ringtone: Ringtone? = null
    private var downloading: String? = null
    private var pending: Received? = null

    private data class Received(val id: String, val name: String, val size: Long, val sha: String, val file: File)

    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        store = CredentialStore(this)
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(18), dp(20), dp(18))
            setBackgroundColor(Color.rgb(11, 17, 24))
        }
        val scroll = ScrollView(this)
        scroll.addView(content)
        setContentView(scroll)
        content.setOnApplyWindowInsetsListener { view, insets ->
            view.setPadding(dp(20), dp(18) + insets.systemWindowInsetTop,
                dp(20), dp(18) + insets.systemWindowInsetBottom)
            insets
        }
        content.addView(TextView(this).apply {
            text = "Jarvix Companion"
            textSize = 27f
            setTextColor(Color.rgb(127, 243, 255))
        })
        status = TextView(this)
        permissions = TextView(this)
        content.addView(status)
        content.addView(permissions)
        invite = EditText(this).apply {
            hint = "Paste a Jarvix pairing invitation"
            maxLines = 4
            setSingleLine(false)
        }
        content.addView(invite)
        content.addView(button("Scan invitation QR") {
            IntentIntegrator(this).setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                .setPrompt("Scan the invitation shown by Jarvix on your PC.")
                .setBeepEnabled(false).initiateScan()
        })
        content.addView(button("Pair with PC") { pair(invite.text.toString()) })
        content.addView(button("Forget this PC") { forget() })
        content.addView(button("Approve phone apps") { chooseApp() })
        content.addView(button("Transfer history") { showHistory() })
        val ringing = getSharedPreferences("phone_permissions", MODE_PRIVATE)
        content.addView(Switch(this).apply {
            text = "Allow the paired PC to ring this phone"
            isChecked = ringing.getBoolean("ring", false)
            setOnCheckedChangeListener { _, allowed -> ringing.edit().putBoolean("ring", allowed).apply() }
        })
        content.addView(button("Cancel active transfer") { cancelled.set(true) })
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            visibility = View.GONE
        }
        content.addView(progress)
        offers = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        content.addView(offers)
        val paired = store.load()
        link = paired?.let { Link(it) }
        status.text = if (link == null) "Pair with Jarvix on your PC." else "Connecting to " + paired?.address
        cacheDir.listFiles()?.filter { it.name.startsWith("jarvix-transfer-") && it.name.endsWith(".part") }
            ?.forEach { it.delete() }
    }

    override fun onResume() {
        super.onResume()
        running = true
        val camera = if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
            "allowed" else "requested when scanning"
        permissions.text = "Camera " + camera + " · Phone actions require review"
        main.removeCallbacks(poll)
        main.post(poll)
    }

    override fun onPause() {
        running = false
        main.removeCallbacks(poll)
        super.onPause()
    }

    override fun onDestroy() {
        cancelled.set(true)
        ringtone?.stop()
        pending?.file?.delete()
        main.removeCallbacksAndMessages(null)
        worker.shutdownNow()
        super.onDestroy()
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    private fun button(title: String, action: () -> Unit) = Button(this).apply {
        text = title
        isAllCaps = false
        setOnClickListener { action() }
    }

    private fun ui(action: () -> Unit) = main.post { if (!isDestroyed) action() }

    private fun task(action: () -> Unit) {
        if (!occupied.compareAndSet(false, true)) {
            status.text = "An operation is already running."
            return
        }
        worker.execute {
            try { action() }
            catch (failure: Exception) { ui { status.text = failure.message ?: "Jarvix operation failed." } }
            finally { occupied.set(false) }
        }
    }

    private val poll = object : Runnable {
        override fun run() {
            if (!running) return
            val client = link
            if (client != null && downloading == null && pending == null && occupied.compareAndSet(false, true)) {
                worker.execute {
                    try {
                        val jobs = client.poll()
                        ui {
                            status.text = "Connected · keep the companion open to receive actions"
                            renderJobs(jobs)
                        }
                    } catch (_: Exception) {
                        ui { status.text = "Waiting for Jarvix on the paired private Wi-Fi address." }
                    } finally { occupied.set(false) }
                }
            }
            main.postDelayed(this, 3500)
        }
    }

    private fun pair(text: String) {
        if (text.length !in 1..4096) {
            status.text = "Paste or scan an invitation from Jarvix."
            return
        }
        task {
            val record = JSONObject(text)
            require(record.getInt("v") == 1) { "This invitation format is unsupported." }
            val identity = Identity.parse(record)
            val paired = Link(identity).pair(record.getString("invite"), Build.MODEL.take(48))
            store.save(paired)
            ui {
                link = Link(paired)
                invite.setText("")
                status.text = "Paired with " + paired.address
            }
        }
    }

    private fun forget() {
        AlertDialog.Builder(this).setTitle("Forget the paired PC?")
            .setMessage("You can pair again with a new invitation. Remove the phone on the Windows Android page to revoke the credential.")
            .setNegativeButton("Cancel", null).setPositiveButton("Forget") { _, _ ->
                cancelled.set(true)
                store.clear()
                link = null
                offers.removeAllViews()
                status.text = "Pair with Jarvix on your PC."
            }.show()
    }

    private fun renderJobs(jobs: JSONArray) {
        offers.removeAllViews()
        for (index in 0 until jobs.length()) {
            val job = jobs.getJSONObject(index)
            val args = job.getJSONObject("args")
            if (job.getString("action") == "ring" && getSharedPreferences("phone_permissions", MODE_PRIVATE).getBoolean("ring", false)) {
                handoff(job.getString("id"), "ring", args)
                continue
            }
            val label = when (job.getString("action")) {
                "transfer" -> "Receive " + args.getString("name")
                "open_app" -> "Open " + args.getString("app")
                "dial" -> "Review dialer"
                "ring" -> "Ring this phone"
                "alarm" -> "Review alarm"
                "timer" -> "Review timer"
                else -> continue
            }
            offers.addView(button(label) { review(job, label) })
        }
    }

    private fun review(job: JSONObject, label: String) {
        val action = job.getString("action")
        val args = job.getJSONObject("args")
        val details = when (action) {
            "transfer" -> args.getString("sender") + "\n" + args.getString("name") + "\n" +
                args.getLong("size").toString() + " bytes"
            "dial" -> "Open the system dialer for +" + args.getString("number") + ". Start the call there."
            "timer" -> "Open a " + (args.getInt("seconds") / 60) + "-minute timer in your clock app."
            "alarm" -> String.format(Locale.ROOT, "Open an alarm for %02d:%02d.", args.getInt("hour"), args.getInt("minute"))
            else -> label
        }
        val id = job.getString("id")
        AlertDialog.Builder(this).setTitle(label).setMessage(details)
            .setNegativeButton("Reject") { _, _ -> task { link?.cancel(id) } }
            .setPositiveButton("Approve") { _, _ ->
                if (action == "transfer") receive(id, args) else handoff(id, action, args)
            }.show()
    }

    private fun chooseApp() {
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = packageManager.queryIntentActivities(launcher, 0)
            .filter { it.activityInfo.packageName != packageName }.distinctBy { it.activityInfo.packageName }
            .sortedBy { it.loadLabel(packageManager).toString().lowercase(Locale.ROOT) }
        AlertDialog.Builder(this).setTitle("Approve a phone app")
            .setItems(apps.map { it.loadLabel(packageManager) }.toTypedArray()) { _, index ->
                val app = apps[index]
                val name = app.loadLabel(packageManager).toString().lowercase(Locale.ROOT)
                getSharedPreferences("approved_apps", MODE_PRIVATE).edit().putString(name, app.activityInfo.packageName).apply()
                status.text = "Approved " + app.loadLabel(packageManager)
            }.show()
    }

    private fun handoff(id: String, action: String, args: JSONObject) {
        val ok = try {
            when (action) {
                "open_app" -> {
                    val name = args.getString("app").lowercase(Locale.ROOT)
                    val target = getSharedPreferences("approved_apps", MODE_PRIVATE).getString(name, null)
                    val launch = target?.let { packageManager.getLaunchIntentForPackage(it) }
                    if (launch == null) throw IllegalArgumentException("Approve that app on the companion first.")
                    startActivity(launch)
                }
                "dial" -> {
                    val number = args.getString("number")
                    require(Regex("[1-9][0-9]{7,14}").matches(number))
                    startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:+" + number)))
                }
                "ring" -> {
                    ringtone?.stop()
                    ringtone = RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE))
                    val ring = ringtone ?: throw IllegalStateException("No ringtone is configured.")
                    ring.play()
                    main.postDelayed({ ring.stop() }, 7000)
                }
                "timer" -> {
                    val seconds = args.getInt("seconds")
                    require(seconds in 60..10800)
                    startActivity(Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, seconds)
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, false))
                }
                "alarm" -> {
                    val h = args.getInt("hour")
                    val m = args.getInt("minute")
                    require(h in 0..23 && m in 0..59)
                    startActivity(Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, h)
                        .putExtra(AlarmClock.EXTRA_MINUTES, m).putExtra(AlarmClock.EXTRA_SKIP_UI, false))
                }
                else -> throw IllegalArgumentException("This action is unsupported.")
            }
            true
        } catch (problem: Exception) {
            status.text = problem.message ?: "Android could not open this action."
            false
        }
        val client = link ?: return
        worker.execute { runCatching { client.finish(id, ok) } }
    }

    private fun receive(id: String, args: JSONObject) {
        val client = link ?: return
        val name = args.getString("name")
        val size = args.getLong("size")
        val hash = args.getString("sha256")
        if (!occupied.compareAndSet(false, true)) {
            status.text = "Wait for the current operation to finish."
            return
        }
        downloading = id
        cancelled.set(false)
        progress.progress = 0
        progress.visibility = View.VISIBLE
        worker.execute {
            try {
                validateFile(name, size, hash)
                client.accept(id)
                var result: File? = null
                var failure: Exception? = null
                repeat(3) {
                    if (result == null && !cancelled.get()) {
                        try {
                            var previous = -1
                            result = client.download(id, name, size, hash, cacheDir, cancelled) { bytes ->
                                val value = if (size == 0L) 100 else (bytes * 100 / size).toInt()
                                if (value != previous) {
                                    previous = value
                                    ui { progress.progress = value }
                                }
                            }
                        } catch (problem: Exception) { failure = problem }
                    }
                }
                val file = result ?: throw (failure ?: IllegalStateException("Transfer cancelled."))
                if (cancelled.get()) { file.delete(); throw IllegalStateException("Transfer cancelled.") }
                ui {
                    pending = Received(id, name, size, hash, file)
                    startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT)
                        .addCategory(Intent.CATEGORY_OPENABLE).setType("application/octet-stream")
                        .putExtra(Intent.EXTRA_TITLE, name), SAVE_FILE)
                }
            } catch (problem: Exception) {
                runCatching { client.cancel(id) }
                ui { status.text = problem.message ?: "Transfer failed." }
            } finally {
                occupied.set(false)
                ui { downloading = null; progress.visibility = View.GONE }
            }
        }
    }

    private fun saveFile(uri: Uri?, received: Received) {
        val client = link
        worker.execute {
            var done = false
            try {
                if (uri != null && !cancelled.get()) {
                    contentResolver.openOutputStream(uri, "w")?.use { output ->
                        received.file.inputStream().use { input -> input.copyTo(output, 65536) }
                    } ?: error("Android could not open the selected destination.")
                    done = true
                    remember(received)
                    client?.finish(received.id, true, received.sha)
                } else client?.cancel(received.id)
                ui { status.text = if (done) "Saved " + received.name else "Transfer cancelled." }
            } catch (problem: Exception) {
                ui { status.text = if (done) "File saved. PC acknowledgement could not be sent."
                    else problem.message ?: "The file could not be saved." }
            } finally { received.file.delete() }
        }
    }

    private fun remember(received: Received) {
        val prefs = getSharedPreferences("transfers", MODE_PRIVATE)
        val old = JSONArray(prefs.getString("history", "[]"))
        val next = JSONArray().put(received.name + " · " + received.size + " bytes")
        for (index in 0 until minOf(old.length(), 39)) next.put(old.getString(index))
        prefs.edit().putString("history", next.toString()).apply()
    }

    private fun showHistory() {
        val entries = JSONArray(getSharedPreferences("transfers", MODE_PRIVATE).getString("history", "[]"))
        val rows = (0 until entries.length()).map { entries.getString(it) }
        AlertDialog.Builder(this).setTitle("Saved transfers")
            .setMessage(rows.joinToString("\n").ifBlank { "No files saved yet." })
            .setPositiveButton("Close", null).show()
    }

    @Deprecated("Called by the Android system and ZXing scanner")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode == SAVE_FILE) {
            val received = pending
            pending = null
            if (received != null) saveFile(if (resultCode == RESULT_OK) data?.data else null, received)
            return
        }
        val scanned = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
        if (scanned != null) scanned.contents?.let { pair(it) }
        else super.onActivityResult(requestCode, resultCode, data)
    }

    companion object { private const val SAVE_FILE = 704 }
}
