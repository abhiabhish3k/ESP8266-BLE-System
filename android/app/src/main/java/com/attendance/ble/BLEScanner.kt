package com.attendance.ble

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.le.*
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import java.util.concurrent.ConcurrentHashMap

private const val TAG = "BLEScanner"

/**
 * BLEScanner
 *
 * Continuously scans for nearby BLE devices and maintains a deduplicated
 * map of [deviceId -> ScannedDevice].  Weak signals (below [minRssi]) are
 * ignored to reduce false positives.
 *
 * Usage:
 *   val scanner = BLEScanner(context, minRssi = -80)
 *   scanner.start { devices -> /* called on each scan batch */ }
 *   scanner.stop()
 *
 * The callback fires every [scanWindowMs] milliseconds with a snapshot of
 * all currently visible devices.
 */
class BLEScanner(
    private val context: Context,
    private val minRssi: Int = -80,          // ignore devices weaker than this
    private val scanWindowMs: Long = 5_000L  // callback interval
) {
    /** Represents one discovered BLE device. */
    data class ScannedDevice(
        /**
         * Stable device identifier sent to the ESP8266 as [device_id].
         *
         * Two sources, in priority order:
         *  1. BLE local name prefixed with "ATT-" (set by the Python beacon
         *     advertiser).  This is rotation-proof and human-readable, and is
         *     the recommended identity for student laptops / Raspberry Pis.
         *  2. Hardware MAC address – used for dedicated BLE peripherals
         *     (fitness trackers, beacons) whose MAC does not rotate.
         *
         * Whatever string is stored here must exactly match the device_id
         * registered in the ESP8266 student list.
         */
        val deviceId: String,
        val rssi: Int,
        val timestamp: Long     // Unix epoch seconds (System.currentTimeMillis / 1000)
    )

    companion object {
        /** Prefix used by the Python beacon advertiser (advertiser.py). */
        private const val BEACON_PREFIX = "ATT-"
    }

    // Live deduplicated map: deviceId → latest scan result
    private val deviceMap = ConcurrentHashMap<String, ScannedDevice>()

    private var bluetoothLeScanner: BluetoothLeScanner? = null
    private var scanJob: Job? = null
    private var scanCallback: ScanCallback? = null
    private var onBatch: ((List<ScannedDevice>) -> Unit)? = null

    private val bleAvailable: Boolean
        get() {
            val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
            return bm?.adapter?.isEnabled == true
        }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    /**
     * Start continuous BLE scanning.
     * [callback] is invoked on the main thread every [scanWindowMs] ms.
     */
    fun start(callback: (List<ScannedDevice>) -> Unit) {
        if (!bleAvailable) {
            Log.w(TAG, "Bluetooth not available or disabled")
            return
        }
        if (!hasPermissions()) {
            Log.w(TAG, "Missing BLE permissions")
            return
        }

        onBatch = callback
        val bm = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        bluetoothLeScanner = bm.adapter.bluetoothLeScanner

        // Low-latency scan settings (balanced mode to save battery slightly)
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_BALANCED)
            .build()

        scanCallback = buildScanCallback()
        bluetoothLeScanner?.startScan(null, settings, scanCallback)
        Log.i(TAG, "BLE scan started (minRssi=$minRssi)")

        // Periodic batch delivery
        scanJob = CoroutineScope(Dispatchers.Main).launch {
            while (isActive) {
                delay(scanWindowMs)
                deliverBatch()
            }
        }
    }

    /** Stop scanning and cancel the batch job. */
    fun stop() {
        scanJob?.cancel()
        scanJob = null
        try {
            scanCallback?.let { bluetoothLeScanner?.stopScan(it) }
        } catch (e: Exception) {
            Log.w(TAG, "stopScan exception: ${e.message}")
        }
        scanCallback = null
        deviceMap.clear()
        Log.i(TAG, "BLE scan stopped")
    }

    /** Returns a snapshot of currently visible devices. */
    fun getDevices(): List<ScannedDevice> = deviceMap.values.toList()

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    private fun deliverBatch() {
        val snapshot = deviceMap.values.toList()
        if (snapshot.isNotEmpty()) {
            Log.d(TAG, "Delivering batch: ${snapshot.size} device(s)")
        }
        onBatch?.invoke(snapshot)
    }

    private fun buildScanCallback(): ScanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val rssi = result.rssi
            if (rssi < minRssi) return // filter weak signals

            val deviceId = extractDeviceId(result) ?: return
            val ts = System.currentTimeMillis() / 1000

            val existing = deviceMap[deviceId]
            // Update only if RSSI improved or entry is new
            if (existing == null || rssi > existing.rssi) {
                deviceMap[deviceId] = ScannedDevice(deviceId, rssi, ts)
            }
        }

        override fun onScanFailed(errorCode: Int) {
            Log.e(TAG, "Scan failed with error code: $errorCode")
        }
    }

    /**
     * Derive the stable device identifier from a [ScanResult].
     *
     * Priority:
     *  1. BLE local name starting with [BEACON_PREFIX] – set by the Python
     *     beacon advertiser.  Rotation-proof and human-readable.
     *  2. Hardware MAC address – fallback for dedicated BLE peripherals
     *     (fitness trackers, beacons) whose MAC does not rotate.
     *
     * Returns null when neither a valid beacon name nor a MAC is available,
     * so the caller can skip the result safely.
     */
    private fun extractDeviceId(result: ScanResult): String? {
        val localName = result.scanRecord?.deviceName
        if (!localName.isNullOrEmpty() && localName.startsWith(BEACON_PREFIX)) {
            return localName
        }
        return result.device.address
    }

    private fun hasPermissions(): Boolean {
        val perms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        } else {
            arrayOf(
                Manifest.permission.BLUETOOTH,
                Manifest.permission.BLUETOOTH_ADMIN,
                Manifest.permission.ACCESS_FINE_LOCATION
            )
        }
        return perms.all {
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
        }
    }
}
