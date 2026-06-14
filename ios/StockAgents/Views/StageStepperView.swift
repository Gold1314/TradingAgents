import SwiftUI

/// The 5-stage stepper with per-stage progress, ported from `buildStepper` /
/// `refreshStepper` in `index.html`. Each card shows the stage glyph, label, a
/// progress bar, and a completed/total count.
struct StageStepperView: View {
    @ObservedObject var viewModel: RunSessionViewModel

    var body: some View {
        let stages = Stage.allCases.filter { stage in
            viewModel.plannedNodes.contains { AgentNode.metadata(for: $0).stage == stage }
        }
        guard !stages.isEmpty else { return AnyView(EmptyView()) }

        return AnyView(
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(stages) { stage in
                        stageCard(stage)
                    }
                }
            }
        )
    }

    private func stageCard(_ stage: Stage) -> some View {
        let progress = viewModel.stageProgress(stage)
        let fraction = progress.total == 0 ? 0 : Double(progress.completed) / Double(progress.total)
        let color = Color(hex: stage.colorHex)
        let isComplete = progress.completed == progress.total && progress.total > 0

        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(stage.icon)
                Text(stage.label)
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
            }
            .foregroundStyle(progress.isActive || isComplete ? color : Theme.textMuted)

            ProgressView(value: fraction)
                .tint(color)

            Text("\(progress.completed)/\(progress.total)")
                .font(.caption2.monospacedDigit())
                .foregroundStyle(Theme.textMuted)
        }
        .padding(12)
        .frame(width: 130, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface.opacity(0.5)))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(progress.isActive ? color : Theme.border, lineWidth: progress.isActive ? 1.5 : 1)
        )
    }
}
