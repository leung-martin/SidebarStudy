package com.sidebarstudy.wear

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.foundation.lazy.rememberScalingLazyListState
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.ButtonDefaults
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.ChipDefaults
import androidx.wear.compose.material.ListHeader
import androidx.wear.compose.material.Scaffold
import androidx.wear.compose.material.Switch
import androidx.wear.compose.material.Text
import androidx.wear.compose.material.TimeText
import androidx.wear.compose.material.ToggleChip
import androidx.wear.compose.material.dialog.Alert
import androidx.wear.compose.material.dialog.Dialog

// the phone and PC apps' dark palette
private val BG = Color(0xFF101219)
private val CARD = Color(0xFF1A1D29)
private val BTN = Color(0xFF252937)
private val TRACK = Color(0xFF2A2E40)

fun Tone.color(): Color = when (this) {
    Tone.DIM -> Color(0xFF8B90A8)
    Tone.INK -> Color(0xFFF5F5FA)
    Tone.GREEN -> Color(0xFF2EE59D)
    Tone.RED -> Color(0xFFFF5470)
    Tone.ORANGE -> Color(0xFFFFA53D)
    Tone.YELLOW -> Color(0xFFFFD84D)
    Tone.PINK -> Color(0xFFFF4FA3)
    Tone.VIOLET -> Color(0xFF8B6CFF)
    Tone.TEAL -> Color(0xFF2ED3C6)
}

private val noRipple = MutableInteractionSource()

@Composable
fun StudyScreen(m: StudyModel, onMic: () -> Unit, onMenu: () -> Unit) {
    Scaffold(timeText = { TimeText() }, modifier = Modifier.background(BG)) {
        Box(Modifier.fillMaxSize().background(BG)) {
            EdgeRing(m.listening, m.flash)
            Column(Modifier.fillMaxSize().padding(horizontal = 18.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Spacer(Modifier.height(26.dp))
                Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween) {
                    RoundIcon("🔊", Tone.TEAL) { m.speak() }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        LevelBar(if (m.card != null && m.hz.text == null) m.level else null)
                        SyncLine(m)
                    }
                    RoundIcon("⋯", Tone.VIOLET, onMenu)
                }
                // the characters: tap anywhere here to talk
                Box(Modifier.weight(1f).fillMaxWidth().clickable(noRipple, null) { onMic() }, contentAlignment = Alignment.Center) {
                    Characters(m)
                }
                val side = listOf(m.english, m.status).filter { it.isNotBlank() }
                if (side.isNotEmpty()) {
                    if (m.english.isNotBlank()) Text(m.english, fontSize = 12.sp, color = Tone.INK.color(), textAlign = TextAlign.Center,
                        maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Bold)
                    if (m.status.isNotBlank()) Text(m.status, fontSize = 10.sp, color = m.statusTone.color(), textAlign = TextAlign.Center,
                        maxLines = 3, overflow = TextOverflow.Ellipsis, lineHeight = 12.sp)
                } else if (m.card != null) {
                    Text(if (m.listening) "🎙 listening" else if (m.inlineMic) "tap the card to talk" else "tap the card, then speak",
                        fontSize = 10.sp, color = if (m.listening) Tone.TEAL.color() else Tone.DIM.color())
                }
                Spacer(Modifier.height(4.dp))
                // inset from the round edge, where the screen is narrower
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.padding(horizontal = 16.dp)) {
                    GradeButton("Forgot", Tone.PINK, Modifier.weight(1f)) { m.grade("hard") }
                    GradeButton("Not bad", Tone.ORANGE, Modifier.weight(1f)) { m.grade("good") }
                    GradeButton("Good", Tone.GREEN, Modifier.weight(1f)) { m.grade("easy") }
                }
                Spacer(Modifier.height(24.dp))
            }
        }
    }
}

@Composable
private fun RoundIcon(text: String, tone: Tone, onClick: () -> Unit) {
    Box(Modifier.size(30.dp).clip(RoundedCornerShape(50)).background(BTN).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Text(text, fontSize = 14.sp, color = tone.color())
    }
}

@Composable
private fun GradeButton(label: String, tone: Tone, modifier: Modifier, onClick: () -> Unit) {
    Box(modifier.height(30.dp).clip(RoundedCornerShape(15.dp)).background(BTN).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Text(label, fontSize = 11.sp, fontWeight = FontWeight.Bold, color = tone.color(), maxLines = 1)
    }
}

@Composable
private fun LevelBar(level: Int?) {
    when {
        level == null -> Spacer(Modifier.height(6.dp))
        level < 0 -> Text("NEW", fontSize = 9.sp, color = Tone.TEAL.color(), fontWeight = FontWeight.Bold)
        else -> {
            val tone = if (level > 3) Tone.GREEN else if (level > 1) Tone.ORANGE else Tone.RED
            Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                repeat(5) { i -> Box(Modifier.width(10.dp).height(4.dp).clip(RoundedCornerShape(2.dp)).background(if (i < level) tone.color() else TRACK)) }
            }
        }
    }
}

@Composable
private fun SyncLine(m: StudyModel) {
    val (text, tone) = syncLabel(m)
    Text(text, fontSize = 8.sp, color = tone.color(), maxLines = 1)
}

fun syncLabel(m: StudyModel): Pair<String, Tone> {
    val up = if (m.pending > 0) "↑ ${m.pending}" else ""
    return when (m.syncState) {
        "not signed in" -> "☁ sign in (⋯)" to Tone.RED
        "expired" -> "☁ sign-in failed" to Tone.RED
        "blocked" -> "⚠ uploads stopped" to Tone.RED
        "downloading" -> "↓ downloading…" to Tone.DIM
        "out of date" -> "↓ out of date" to Tone.ORANGE
        "checking" -> "☁ checking…" to Tone.DIM
        "offline" -> "☁ offline${if (up.isNotEmpty()) " · $up" else ""}" to Tone.ORANGE
        "" -> "☁ …" to Tone.DIM
        else -> if (up.isNotEmpty()) "$up to upload" to Tone.ORANGE else "✓ synced" to Tone.GREEN
    }
}

/** A thin ring around the round screen: teal while listening, and a flash of the result color. */
@Composable
private fun EdgeRing(listening: Boolean, flash: Pair<Int, Tone>) {
    val glow = remember { Animatable(0f) }
    LaunchedEffect(flash.first) {
        if (flash.first == 0) return@LaunchedEffect
        glow.snapTo(1f)
        glow.animateTo(0f, tween(700))
    }
    Canvas(Modifier.fillMaxSize()) {
        val w = 3.dp.toPx()
        val inset = w / 2 + 1
        if (listening) drawArc(Tone.TEAL.color(), 0f, 360f, false, Offset(inset, inset), Size(size.width - 2 * inset, size.height - 2 * inset), style = Stroke(w))
        if (glow.value > 0f) {
            val g = 7.dp.toPx()
            drawArc(flash.second.color().copy(alpha = glow.value), 0f, 360f, false, Offset(g / 2, g / 2),
                Size(size.width - g, size.height - g), style = Stroke(g))
        }
    }
}

/** The card's characters, pinyin above each (and, after an answer, what you said below), as big as fits. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Characters(m: StudyModel) {
    val hz = m.hz
    val layout = m.layout
    if (hz.text != null || layout == null) {
        Text(hz.text ?: "⏳", fontSize = 40.sp)
        return
    }
    val compare = hz.reveal && hz.said != null
    val rubyF = if (hz.reveal) 0.62f else 0.36f
    val chF = if (hz.reveal) 0.72f else 1f
    val saidF = if (compare) 0.34f else 0f
    val measurer = rememberTextMeasurer()
    val density = LocalDensity.current
    BoxWithConstraints(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        val maxW = constraints.maxWidth
        val maxH = constraints.maxHeight
        // the biggest size (dp) where the characters fit, measured exactly as they'll be drawn;
        // short words stay on one line while the characters are big, long sentences wrap
        val sizeDp = remember(hz, layout, maxW, maxH) {
            fun fits(sizeDp: Int): Boolean {
                val px = with(density) { Dp(sizeDp.toFloat()).toSp() }
                val pad = with(density) { 2.dp.roundToPx() }
                fun width(text: String?, f: Float) =
                    if (text.isNullOrEmpty()) 0 else measurer.measure(text, hzStyle(px, f)).size.width
                fun height(f: Float) = measurer.measure("中", hzStyle(px, f)).size.height
                var lines = 1
                var lineW = 0
                for ((ch, k) in layout.parts) {
                    val w = maxOf(width(ch, chF), width(hz.ruby[k], rubyF), if (compare) width(hz.said?.get(k)?.first, saidF) else 0) + pad
                    if (lineW > 0 && lineW + w > maxW) { lines++; lineW = 0 }
                    lineW += w
                }
                val rowH = height(rubyF) + height(chF) + (if (compare) height(saidF) else 0)
                return lines * rowH <= maxH && (lines == 1 || sizeDp <= 30)
            }
            var lo = 12
            var hi = 64
            while (lo < hi) {  // largest size that fits
                val mid = (lo + hi + 1) / 2
                if (fits(mid)) lo = mid else hi = mid - 1
            }
            lo.toFloat()
        }
        val px = with(density) { Dp(sizeDp).toSp() }
        FlowRow(horizontalArrangement = Arrangement.Center, verticalArrangement = Arrangement.Center) {
            for ((ch, k) in layout.parts) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(horizontal = 1.dp)) {
                    if (k < 0) {
                        Text("", style = hzStyle(px, rubyF))
                        Text(ch, style = hzStyle(px, chF), color = Tone.INK.color())
                        if (compare) Text("", style = hzStyle(px, saidF))
                        return@Column
                    }
                    var chTone = if (k in hz.matched) Tone.GREEN else if (hz.final) Tone.RED else Tone.INK
                    var pyTone = hz.rubyTone ?: if (k in hz.matched) Tone.GREEN else if (hz.final) Tone.RED else Tone.DIM
                    if (k in hz.toneOff) { chTone = Tone.YELLOW; if (!compare) pyTone = Tone.YELLOW }
                    Text(hz.ruby[k] ?: "", style = hzStyle(px, rubyF), color = pyTone.color(), maxLines = 1)
                    Text(ch, style = hzStyle(px, chF), color = chTone.color())
                    if (compare) {
                        val (s, state) = hz.said?.get(k) ?: ("—" to "bad")
                        val tone = when (state) { "ok" -> Tone.GREEN; "tone" -> Tone.YELLOW; "skip" -> Tone.DIM; else -> Tone.RED }
                        Text(if (state == "ok") "✓" else s, style = hzStyle(px, saidF), color = tone.color(), maxLines = 1)
                    }
                }
            }
        }
    }
}

/** One text style for drawing and measuring the characters' rows (f: share of the base size). */
private fun hzStyle(base: TextUnit, f: Float) = TextStyle(fontSize = base * f, fontWeight = FontWeight.Bold, lineHeight = base * f * 1.15f)

@Composable
fun MasterDialog(m: StudyModel, show: Boolean, onDismiss: () -> Unit) {
    val card = m.card
    Dialog(showDialog = show && card != null, onDismissRequest = onDismiss) {
        Alert(
            title = { Text("Master this card?", textAlign = TextAlign.Center) },
            negativeButton = { Button(onClick = onDismiss, colors = ButtonDefaults.secondaryButtonColors()) { Text("✗") } },
            positiveButton = { Button(onClick = { m.master(); onDismiss() }) { Text("✓") } },
        ) {
            Text("${card?.simplified ?: ""}  ${card?.pinyin ?: ""}\n\nSends Good to YoYo three times, so it's mastered and you stop seeing it.",
                textAlign = TextAlign.Center, fontSize = 12.sp)
        }
    }
}

@Composable
fun MenuScreen(m: StudyModel, onSignIn: () -> Unit, onDone: () -> Unit) {
    var confirmMaster by remember { mutableStateOf(false) }
    val list = rememberScalingLazyListState()
    Scaffold(timeText = { TimeText() }) {
        ScalingLazyColumn(state = list, modifier = Modifier.fillMaxSize().background(BG)) {
            item { ListHeader { Text("Sidebar Study") } }
            if (m.card != null) {
                item { MenuChip("★ Master this card", m.card?.simplified ?: "", Tone.YELLOW) { confirmMaster = true } }
                item { MenuChip("🔊 Hear it", m.card?.pinyin?.takeIf { m.hz.reveal } ?: "") { m.speak(); onDone() } }
            }
            item {
                val (text, _) = syncLabel(m)
                MenuChip("Sync now", if (m.blocked.isNotEmpty()) m.blocked else text) { m.syncNow() }
            }
            item {
                MenuChip("Deck", m.deck) {
                    val names = m.deckNames
                    m.chooseDeck(names[(names.indexOf(m.deck) + 1) % names.size])
                }
            }
            item {
                val options = listOf(1.5f, 2f, 3f, 4f, 5f, 7f)
                MenuChip("Next card after", "${m.delaySec.toString().removeSuffix(".0")} s") {
                    m.setDelay(options[(options.indexOf(m.delaySec) + 1) % options.size])
                }
            }
            item {
                val options = listOf(30, 60, 120, 300)
                MenuChip("Mic turns off after", if (m.listenWindow < 60) "${m.listenWindow} s quiet" else "${m.listenWindow / 60} min quiet") {
                    m.changeListenWindow(options[(options.indexOf(m.listenWindow) + 1) % options.size])
                }
            }
            item { Toggle("Show English first", m.showEnglish) { m.toggleShowEnglish() } }
            item { Toggle("Review mastered", m.reviewMastered) { m.toggleReviewMastered() } }
            item {
                if (m.signedIn) MenuChip("Sign out", m.email) { m.signOut() }
                else MenuChip("Sign in to YoYo", "email + password", Tone.TEAL, onSignIn)
            }
            item { Text("✓ ${m.right}   ✗ ${m.passed}   🔥 ${m.streak}", fontSize = 12.sp, color = Tone.DIM.color()) }
        }
    }
    MasterDialog(m, confirmMaster) { confirmMaster = false; if (m.card == null || m.hz.reveal) onDone() }
}

@Composable
private fun MenuChip(label: String, secondary: String, tone: Tone = Tone.INK, onClick: () -> Unit) {
    Chip(
        label = { Text(label, color = tone.color(), maxLines = 1) },
        secondaryLabel = if (secondary.isNotEmpty()) ({ Text(secondary, maxLines = 2, overflow = TextOverflow.Ellipsis) }) else null,
        onClick = onClick,
        colors = ChipDefaults.chipColors(backgroundColor = CARD),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
private fun Toggle(label: String, checked: Boolean, onChange: () -> Unit) {
    ToggleChip(
        checked = checked,
        onCheckedChange = { onChange() },
        label = { Text(label) },
        toggleControl = { Switch(checked = checked) },
        modifier = Modifier.fillMaxWidth(),
    )
}
