package ai.jarvix.companion

import org.junit.Assert.*
import org.junit.Test

class ValidationTest {
    @Test fun onlyPrivateLanAddresses() {
        assertTrue(privateAddress("192.168.1.8:8765"))
        assertTrue(privateAddress("172.20.2.1:4430"))
        assertFalse(privateAddress("172.32.0.1:8765"))
        assertFalse(privateAddress("192.168.1.8:0"))
        assertFalse(privateAddress("example.com:8765"))
    }
    @Test fun exactTransferBoundary() {
        validateFile("slides.pdf", 2_000_000_000L, "a".repeat(64))
        try {
            validateFile("slides.pdf", 2_000_000_001L, "a".repeat(64))
            fail("oversized transfer")
        } catch (_: IllegalArgumentException) { }
        try {
            validateFile("../unsafe", 1, "a".repeat(64))
            fail("unsafe file name")
        } catch (_: IllegalArgumentException) { }
    }
}
