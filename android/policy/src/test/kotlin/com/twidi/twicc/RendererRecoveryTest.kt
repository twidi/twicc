package com.twidi.twicc

import org.junit.Assert.*
import org.junit.Test

class RendererRecoveryTest {
    @Test fun stoppedRendererWaitsForTheForegroundAndRecoversOnlyOnce() {
        val recovery = RendererRecovery()
        recovery.stopped()
        assertFalse(recovery.takeAttempt(1000, false))
        assertTrue(recovery.takeAttempt(2000, true))
        assertFalse(recovery.takeAttempt(2001, true))
    }

    @Test fun repeatedCrashesDoNotCreateAnAutomaticReloadLoop() {
        val recovery = RendererRecovery()
        recovery.stopped()
        assertTrue(recovery.takeAttempt(1000, true))
        recovery.stopped()
        assertFalse(recovery.takeAttempt(2000, true))
        assertTrue(recovery.takeAttempt(61000, true))
    }

    @Test fun manualRetryAllowsAnotherRecoveryAndSavedBudgetSurvivesRecreation() {
        val recovery = RendererRecovery()
        recovery.stopped()
        assertTrue(recovery.takeAttempt(1000, true))
        val restored = RendererRecovery(recovery.lastAttemptMs)
        restored.stopped()
        assertFalse(restored.takeAttempt(2000, true))
        restored.reset()
        restored.stopped()
        assertTrue(restored.takeAttempt(2001, true))
    }
}
