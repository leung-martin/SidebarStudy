package com.sidebarstudy.wear

import android.Manifest
import android.app.RemoteInput
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognizerIntent
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.navigation.SwipeDismissableNavHost
import androidx.wear.compose.navigation.composable
import androidx.wear.compose.navigation.rememberSwipeDismissableNavController
import androidx.wear.input.RemoteInputIntentHelper

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { App(viewModel()) } }
    }

    // debug builds only: `adb shell am start -n com.sidebarstudy.wear/.MainActivity --esa heard 你好,ni hao`
    // grades text as if the recognizer heard it, so grading can be tested without speaking
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (!BuildConfig.DEBUG) return
        val m = ViewModelProvider(this)[StudyModel::class.java]
        intent.getStringExtra("show")?.let { m.debugShow(it) }  // --es show 你好: jump to a card containing it
        intent.getStringArrayExtra("heard")?.let { m.heardFromSystem(it.toList()) }
    }

    @Composable
    private fun App(m: StudyModel) {
        val nav = rememberSwipeDismissableNavController()
        var askedMic by remember { mutableStateOf(false) }

        // the screen stays on while the mic is listening (it closes itself after the quiet time)
        LaunchedEffect(m.listening) {
            if (m.listening) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }

        // watches without an inline recognizer: the system's voice input screen, one phrase per tap
        val systemMic = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
            res.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.let { m.heardFromSystem(it) }
        }
        val micPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (granted) m.toggleMic()
        }
        val signIn = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
            val results = res.data?.let { RemoteInput.getResultsFromIntent(it) } ?: return@rememberLauncherForActivityResult
            val email = results.getCharSequence("email")?.toString()?.trim().orEmpty()
            val password = results.getCharSequence("password")?.toString().orEmpty()
            if (email.isNotEmpty() && password.isNotEmpty()) m.signIn(email, password)
        }

        fun mic() {
            if (!m.inlineMic) { runCatching { systemMic.launch(recognizerIntent()) }; return }
            val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            if (granted || askedMic) m.toggleMic() else { askedMic = true; micPermission.launch(Manifest.permission.RECORD_AUDIO) }
        }

        SwipeDismissableNavHost(navController = nav, startDestination = "study") {
            composable("study") { StudyScreen(m, onMic = ::mic, onMenu = { nav.navigate("menu") }) }
            composable("menu") {
                MenuScreen(m, onSignIn = {
                    val intent = RemoteInputIntentHelper.createActionRemoteInputIntent()
                    RemoteInputIntentHelper.putRemoteInputsExtra(intent, listOf(
                        RemoteInput.Builder("email").setLabel("YoYo email").build(),
                        RemoteInput.Builder("password").setLabel("YoYo password").build(),
                    ))
                    signIn.launch(intent)
                }, onDone = { nav.popBackStack() })
            }
        }
    }
}
