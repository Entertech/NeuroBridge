#define EEG_ALGORITHM 1
#define HR_ALGORITHM 1
#define SCEEG_ALGORITHM 1
#define PEPR_ALGORITHM 1


#if EEG_ALGORITHM
#define PLEASURE_ALGORITHM 1
#define RELAXATION_ALGORITHM 1
#define SLEEP_ALGORITHM 1
#define ATTENTION_ALGORITHM 1
#define FLOW_ALGORITHM 1
#endif

#if HR_ALGORITHM
#define AROUSAL_ALGORITHM 1
#define PRESSURE_ALGORITHM 1
#define COHERENCE_ALGORITHM 1
#endif

#if SCEEG_ALGORITHM
#define SLEEP_ALGORITHM 1
#define RELAXATION_ALGORITHM 1
#endif

#if PEPR_ALGORITHM
#define PRESSURE_ALGORITHM 1
#define COHERENCE_ALGORITHM 1
#endif


#if EEG_ALGORITHM

#include "EEG.h"

#endif

#if HR_ALGORITHM

#include "HR.h"

#endif

#if SCEEG_ALGORITHM

#include "SCEEG.h"

#endif

#if PEPR_ALGORITHM

#include "PEPR.h"

#endif

#ifdef AROUSAL_ALGORITHM
#if AROUSAL_ALGORITHM

#include "Arousal.h"

#endif
#endif

#ifdef PRESSURE_ALGORITHM
#if PRESSURE_ALGORITHM

#include "Pressure.h"

#endif
#endif

#ifdef COHERENCE_ALGORITHM
#if COHERENCE_ALGORITHM

#include "Coherence.h"

#endif
#endif

#ifdef RELAXATION_ALGORITHM
#if RELAXATION_ALGORITHM

#include "Relaxation.h"

#endif
#endif

#ifdef ATTENTION_ALGORITHM
#if ATTENTION_ALGORITHM

#include "Attention.h"

#endif
#endif

#ifdef SLEEP_ALGORITHM
#if SLEEP_ALGORITHM

#include "Sleep.h"

#endif
#endif

#ifdef FLOW_ALGORITHM
#if FLOW_ALGORITHM

#include "Meditation.h"

#endif
#endif

#ifdef PLEASURE_ALGORITHM
#if PLEASURE_ALGORITHM

#include "Pleasure.h"

#endif
#endif

#include "Affective.h"
#include "SessionCache.h"
#include "Device.h"
#include "AnalysisTool.h"
#include "VectorExtention.h"


using namespace basic;

class AffectiveAlgorithm::AffectiveAlgorithmImpl {
public:
    size_t eegIndex;
    size_t hrIndex;
    double triggerTime;
    bool AROURSL_ENABLE = false;
    bool ATTENTION_ENABLE = false;
    bool RELAXATION_ENABLE = false;
    bool PRESSURE_ENABLE = false;
    bool COHERENCE_ENABLE = false;
    bool SLEEP_ENABLE = false;
    bool FLOW_ENABLE = false;
    bool PLEASURE_ENABLE = false;
    bool EEG_ENABLE = false;
    bool HR_ENABLE = false;
    bool PEPR_ENABLE = false;   
    bool SCEEG_ENABLE = false;

    AffectiveAlgorithmImpl() {
        triggerTime = 0.6f;
        cache.eegProgress = 0;
        cache.hr = 0;
        cache.hrSyncCor = 0;
        cache.hrv = 0;
        cache.rr = 0.;
        cache.bcgQuality = dsp::BCGQuality::BCG_NONE;
        cache.rwQuality = dsp::RWQuality::RW_NONE;
        cache.hrQuality = dsp::HRQuality::INVALID;
        cache.eegQuality = dsp::EEGQuality::NONE;
        eegIndex = 0;
        hrIndex = 0;
        sleepStateTmp = 0;
        sleepDegreeTmp = 0;
        sleepStageTmp = 0;
        sleepSpindleTmp = 0;

    }

    ~AffectiveAlgorithmImpl() {

    }

#if PEPR_ALGORITHM

    PEPRAffectiveRes appendPEPR(const std::vector<uint8_t> &peprRaw) {
        PEPRAffectiveRes res;
        if (!PEPR_ENABLE) {
            return res;
        }
        auto peprData = tools::peprDataAnalysis(peprRaw, static_cast<int>(peprRaw.size() / 15));
        auto peData = peprData.pe;
        auto prData = peprData.pr;
        auto pepeTriggerRes = peprDP.trigger(cache, peData, prData);

        double pressure = 0.;
        double coherence = 0.;

#if PRESSURE_ALGORITHM
        if (PRESSURE_ENABLE)
            pressure = pressureAC.trigger(cache);
#endif


#if COHERENCE_ALGORITHM
        if (COHERENCE_ENABLE)
            coherence = coherenceAC.trigger(cache);
#endif


        res.bcgWave.assign(pepeTriggerRes.bcgWave.cbegin(), pepeTriggerRes.bcgWave.cend());
        res.rwWave.assign(pepeTriggerRes.rwWave.cbegin(), pepeTriggerRes.rwWave.cend());
        res.bcgQuality = static_cast<int>(pepeTriggerRes.bcgQuality);
        res.rwQuality = static_cast<int>(pepeTriggerRes.rwQuality);
        res.hr = pepeTriggerRes.hr;
        res.hrv = pepeTriggerRes.hrv;
        res.rr = pepeTriggerRes.rr;
        res.pressure = pressure;
        res.coherence = coherence;

        return res;
    }

#endif

#if SCEEG_ALGORITHM

    SCEEGAffectiveRes appendSCEEG(const std::vector<uint8_t> &eegRaw) {
        SCEEGAffectiveRes res;
        if (!SCEEG_ENABLE) {
            return res;
        }
        eegIndex++;
        auto sceegData = tools::singleEEGDataAnalysis(eegRaw, static_cast<int>(eegRaw.size() / 17));

        std::vector<double> sceegSplit;
        for (auto &e: sceegData.eeg) {
            sceegSplit.push_back(static_cast<double>(e));
        }
        auto sceegTriggerRes = sceegDP.trigger(cache, sceegSplit);

        nc::NdArray<int> sceegqTriggerNdArray = {sceegTriggerRes.eegQuality};


        double relaxation = 0.;

#if RELAXATION_ALGORITHM
        if (RELAXATION_ENABLE)
            relaxation = relaxationAC.trigger(cache);
#endif


        SleepTriggerRes sleepTriggerRes{};
        sleepTriggerRes.sleepDegree = sleepDegreeTmp;
        sleepTriggerRes.sleepState = sleepStateTmp;
        sleepTriggerRes.sleepStage = sleepStageTmp;
        sleepTriggerRes.sleepSpindle = sleepSpindleTmp;
        sleepTriggerRes.updateFlag = false;
#if SLEEP_ALGORITHM
        if (SLEEP_ENABLE) {
            sleepEEGBuffer.reserve(sleepEEGBuffer.size() + sceegSplit.size());
            sleepEEGBuffer.insert(sleepEEGBuffer.cend(), sceegSplit.cbegin(), sceegSplit.cend());

            auto eegFs = int(10 * triggerTime * device.eegFs());
            if (sleepEEGBuffer.size() > eegFs) {
                sleepEEGBuffer = tools::cutArrs(sleepEEGBuffer, static_cast<int>(sleepEEGBuffer.size() - eegFs),
                                                sleepEEGBuffer.size());
            }

            if (sleepEEGBuffer.size() >= eegFs) {

                auto sleepTriggerResTemp = sleepAC.trigger(cache, sleepEEGBuffer);

                sleepTriggerRes.sleepDegree = sleepTriggerResTemp.sleepDegree;
                sleepTriggerRes.sleepState = sleepTriggerResTemp.sleepState;
                sleepTriggerRes.sleepStage = sleepTriggerResTemp.sleepStage;
                sleepTriggerRes.sleepSpindle = sleepTriggerResTemp.sleepSpindle;
                sleepTriggerRes.updateFlag = true;
                sleepDegreeTmp = sleepTriggerResTemp.sleepDegree;
                sleepStateTmp = sleepTriggerResTemp.sleepState;
                sleepStageTmp = sleepTriggerResTemp.sleepStage;
                sleepSpindleTmp = sleepTriggerResTemp.sleepSpindle;
                
                sleepEEGBuffer.clear();

            }
        }

#endif

        SCEEGTriggerRes eegRes;
        eegRes.eegWave = sceegTriggerRes.eegWave;
        eegRes.eegQuality = double(sceegTriggerRes.eegQuality);
        eegRes.eegAlphaPower = sceegTriggerRes.eegAlphaPower;
        eegRes.eegBetaPower = sceegTriggerRes.eegBetaPower;
        eegRes.eegThetaPower = sceegTriggerRes.eegThetaPower;
        eegRes.eegDeltaPower = sceegTriggerRes.eegDeltaPower;
        eegRes.eegGammaPower = sceegTriggerRes.eegGammaPower;

        res.eeg = eegRes;
        res.relaxation = relaxation;
        res.sleep = sleepTriggerRes;

        return res;

    }

#endif

#if EEG_ALGORITHM

    EEGAffectiveRes appendEEG(const std::vector<uint8_t> &eegRaw, bool isEar) {
        EEGAffectiveRes res;
        if (!EEG_ENABLE) {
            return res;
        }
        eegIndex++;
        auto eegData = tools::doubleEEGDataAnalysis(eegRaw, static_cast<int>(eegRaw.size() / 20));

        std::vector<double> eeglSplit;
        for (auto &e: eegData.left) {
            eeglSplit.push_back(static_cast<double>(e));
        }

        std::vector<double> eegrSplit;
        for (auto &e: eegData.right) {
            eegrSplit.push_back(static_cast<double>(e));
        }
        auto eegTriggerRes = eegDP.trigger(cache, eeglSplit, eegrSplit, isEar);

        double relaxation = 0.;
        double pleasure = 0.;
        double attention = 0.;

#if ATTENTION_ALGORITHM
        if (ATTENTION_ENABLE)
            attention = attentionAC.trigger(cache);
#endif

#if RELAXATION_ALGORITHM
        if (RELAXATION_ENABLE)
            relaxation = relaxationAC.trigger(cache);
#endif

#if PLEASURE_ALGORITHM
        if (PLEASURE_ENABLE)
            pleasure = pleasureAC.trigger(cache);
#endif

        FlowTriggerRes flowRes{};
        flowRes.meditation = 0.;
        flowRes.meditationTips = 0.;
#if FLOW_ALGORITHM
        if (FLOW_ENABLE) {
            auto flow = flowAC.trigger(cache, eeglSplit, eegrSplit);
            flowRes.meditation = flow.meditation;
            flowRes.meditationTips = flow.meditationTips;
        }
#endif


        SleepTriggerRes sleepTriggerRes{};
        sleepTriggerRes.sleepDegree = sleepDegreeTmp;
        sleepTriggerRes.sleepState = sleepStateTmp;
        sleepTriggerRes.sleepStage = sleepStageTmp;
        sleepTriggerRes.sleepSpindle = sleepSpindleTmp;
        sleepTriggerRes.updateFlag = false;
#if SLEEP_ALGORITHM
        if (SLEEP_ENABLE) {
            sleepEEGBuffer.reserve(sleepEEGBuffer.size() + eeglSplit.size());
            sleepEEGBuffer.insert(sleepEEGBuffer.cend(), eeglSplit.cbegin(), eeglSplit.cend());

            auto eegFs = int(10 * triggerTime * device.eegFs());
            if (sleepEEGBuffer.size() > eegFs) {
                sleepEEGBuffer = tools::cutArrs(sleepEEGBuffer, static_cast<int>(sleepEEGBuffer.size() - eegFs),
                                                sleepEEGBuffer.size());
            }

            if (sleepEEGBuffer.size() >= eegFs) {

                auto sleepTriggerResTemp = sleepAC.trigger(cache, sleepEEGBuffer);

                sleepTriggerRes.sleepDegree = sleepTriggerResTemp.sleepDegree;
                sleepTriggerRes.sleepState = sleepTriggerResTemp.sleepState;
                sleepTriggerRes.sleepStage = sleepTriggerResTemp.sleepStage;
                sleepTriggerRes.sleepSpindle = sleepTriggerResTemp.sleepSpindle;
                sleepTriggerRes.updateFlag = true;
                sleepDegreeTmp = sleepTriggerResTemp.sleepDegree;
                sleepStateTmp = sleepTriggerResTemp.sleepState;
                sleepStageTmp = sleepTriggerResTemp.sleepStage;
                sleepSpindleTmp = sleepTriggerResTemp.sleepSpindle;
                sleepEEGBuffer.clear();
            }
        }
#endif


        EEGTriggerRes eegRes;
        eegRes.eeglWave = eegTriggerRes.eeglWave;
        eegRes.eegrWave = eegTriggerRes.eegrWave;
        eegRes.eegQuality = double(eegTriggerRes.eegQuality);
        eegRes.eegAlphaPower = eegTriggerRes.eegAlphaPower;
        eegRes.eegBetaPower = eegTriggerRes.eegBetaPower;
        eegRes.eegThetaPower = eegTriggerRes.eegThetaPower;
        eegRes.eegDeltaPower = eegTriggerRes.eegDeltaPower;
        eegRes.eegGammaPower = eegTriggerRes.eegGammaPower;
        eegRes.eegLowBetaPower = eegTriggerRes.eegLowBetaPower;
        eegRes.eegHighBetaPower = eegTriggerRes.eegHighBetaPower;

        res.eeg = eegRes;
        res.attention = attention;
        res.relaxation = relaxation;
        res.pleasure = pleasure;
        res.sleep = sleepTriggerRes;
        res.flow = flowRes;

        return res;
    }

#endif

#if HR_ALGORITHM

    HRAffectiveRes appendHR(const std::vector<uint8_t> &hrRaw) {
        HRAffectiveRes res;
        if (!HR_ENABLE) {
            return res;
        }
        hrIndex++;
        auto hrData = tools::hrDataAnalysis(hrRaw, hrRaw.size());

        auto hrSplit = hrData.hr;

        auto hrTriggerRes = hrDP.trigger(cache, hrSplit);

        double pressure = 0.;
        double arousal = 0.;
        double coherence = 0.;

#if PRESSURE_ALGORITHM
        if (PRESSURE_ENABLE)
            pressure = pressureAC.trigger(cache);
#endif

#if AROUSAL_ALGORITHM
        if (AROURSL_ENABLE)
            arousal = arousalAC.trigger(cache);
#endif

#if COHERENCE_ALGORITHM
        if (COHERENCE_ENABLE)
            coherence = coherenceAC.trigger(cache);
#endif

        HRTriggerRes hrRes{};
        hrRes.hr = hrTriggerRes.hr;
        hrRes.hrv = hrTriggerRes.hrv;

        
        res.hr = hrRes;
        res.arousal = arousal;
        res.pressure = pressure;
        res.coherence = coherence;

        return res;
    }

#endif


    ArousalReportRes getArousalReport() {

        ArousalReportRes res;
#ifdef AROUSAL_ALGORITHM
#if AROUSAL_ALGORITHM
        auto report = arousalAC.report();
        res.arousalAvg = report.arousalAvg;
        res.arousalRec.assign(report.arousalRec.cbegin(), report.arousalRec.cend());
#endif
#endif
        return res;
    }


    CoherenceReportRes getCoherenceReport() {

        CoherenceReportRes res;
#ifdef COHERENCE_ALGORITHM
#if COHERENCE_ALGORITHM
        auto report = coherenceAC.report();
        res.coherenceAvg = report.coherenceAvg;
        res.coherenceRec.assign(report.coherenceRec.cbegin(), report.coherenceRec.cend());
        res.flagRec.assign(report.flagRec.cbegin(), report.flagRec.cend());
        res.coherenceDuration = report.coherenceDuration;
#endif
#endif
        return res;
    }


    PleasureReportRes getPleasureReport() {
        PleasureReportRes res;
#ifdef PLEASURE_ALGORITHM
#if PLEASURE_ALGORITHM
        auto report = pleasureAC.report();
        res.pleasureAvg = report.pleasureAvg;
        res.pleasureRec.assign(report.pleasureRec.cbegin(), report.pleasureRec.cend());
#endif
#endif
        return res;
    }


    PressureReportRes getPressureReport() {
        PressureReportRes res;
#ifdef PRESSURE_ALGORITHM
#if PRESSURE_ALGORITHM
        auto report = pressureAC.report();
        res.pressureAvg = report.pressureAvg;
        res.pressureRec.assign(report.pressureRec.cbegin(), report.pressureRec.cend());
#endif
#endif
        return res;
    }


    RelaxationReportRes getRelaxationReport() {

        RelaxationReportRes res;
#ifdef RELAXATION_ALGORITHM
#if RELAXATION_ALGORITHM
        auto report = relaxationAC.report();
        res.relaxationAvg = report.relaxationAvg;
        res.relaxationRec.assign(report.relaxationRec.cbegin(), report.relaxationRec.cend());
#endif
#endif
        return res;
    }


    AttentionReportRes getAttentionReport() {
        AttentionReportRes res;
#ifdef ATTENTION_ALGORITHM
#if ATTENTION_ALGORITHM
        auto report = attentionAC.report();

        res.attentionAvg = report.attentionAvg;
        res.attentionRec.assign(report.attentionRec.cbegin(), report.attentionRec.cend());
#endif
#endif
        return res;
    }


    int getSleepLength() {
#ifdef SLEEP_ALGORITHM
#if SLEEP_ALGORITHM
        return sleepAC.reportLength();
#endif
#endif
        return 0;
    }

    SleepReportRes getSleepReport() {
        SleepReportRes res;
#ifdef SLEEP_ALGORITHM
#if SLEEP_ALGORITHM
        auto report = sleepAC.report();
        res.sleepCurve.assign(report.sleepCurve.begin(), report.sleepCurve.end());
        res.sleepStage.assign(report.sleepStage.begin(), report.sleepStage.end());
        res.eegAlphaCurve.assign(report.eegAlphaCurve.begin(), report.eegAlphaCurve.end());
        res.eegBetaCurve.assign(report.eegBetaCurve.begin(), report.eegBetaCurve.end());
        res.eegThetaCurve.assign(report.eegThetaCurve.begin(), report.eegThetaCurve.end());
        res.eegDeltaCurve.assign(report.eegDeltaCurve.begin(), report.eegDeltaCurve.end());
        res.eegGammaCurve.assign(report.eegGammaCurve.begin(), report.eegGammaCurve.end());
        res.eegHighBetaDBCurve.assign(report.eegHighBetaDBCurve.begin(), report.eegHighBetaDBCurve.end());
        res.eegLowBetaDBCurve.assign(report.eegLowBetaDBCurve.begin(), report.eegLowBetaDBCurve.end());
        res.eegQualityRec.assign(report.eegQualityRec.begin(), report.eegQualityRec.end());
        res.movementRec.assign(report.movementRec.begin(), report.movementRec.end());
        res.arousalRec.assign(report.arousalRec.begin(), report.arousalRec.end());
        res.spindleRec.assign(report.spindleRec.begin(), report.spindleRec.end());
        res.sleepPoint = report.sleepPoint;
        res.sleepLatency = report.sleepLatency;
        res.awakeDuration = report.awakeDuration;
        res.remDuration = report.remDuration;
        res.lightDuration = report.lightDuration;
        res.deepDuration = report.deepDuration;
        res.movementCount = report.movementCount;
        res.arousalCount = report.arousalCount;
        res.disturbTolerance = report.disturbTolerance;
        
#endif
#endif
        return res;
    }


    FlowReportRes getFlowReport() {
        FlowReportRes res;
#ifdef FLOW_ALGORITHM
#if FLOW_ALGORITHM
        auto report = flowAC.report();
        res.flowBackNum = report.flowBackNum;
        res.flowCombo = report.flowCombo;
        res.flowDepth = report.flowDepth;
        res.flowDuration = report.flowDuration;
        res.flowLatency = report.flowLatency;
        res.flowLossNum = report.flowLossNum;
        res.flowPercent = report.flowPercent;
        res.flowAvg = report.meditationAvg;
        res.flowRec.assign(report.meditationRec.cbegin(), report.meditationRec.cend());
        res.flowTipsRec.assign(report.meditationTipsRec.cbegin(), report.meditationTipsRec.cend());
#endif
#endif
        return res;
    }


    HRReportRes getHRReport() {

        HRReportRes res;
#if HR_ALGORITHM
        auto report = hrDP.report();
        res.hrRec.assign(report.hrRec.cbegin(), report.hrRec.cend());
        res.hrvRec.assign(report.hrvRec.cbegin(), report.hrvRec.cend());
#endif
        return res;
    }


    EEGReprotRes getEEGReport() {

        EEGReprotRes res;
#if EEG_ALGORITHM
        auto report = eegDP.report();
        res.eegAlphaRec.assign(report.eegAlphaRec.cbegin(), report.eegAlphaRec.cend());
        res.eegBetaRec.assign(report.eegBetaRec.cbegin(), report.eegBetaRec.cend());
        res.eegThetaRec.assign(report.eegThetaRec.cbegin(), report.eegThetaRec.cend());
        res.eegDeltaRec.assign(report.eegDeltaRec.cbegin(), report.eegDeltaRec.cend());
        res.eegGammaRec.assign(report.eegGammaRec.cbegin(), report.eegGammaRec.cend());
        res.eegLowBetaRec.assign(report.eegLowBetaRec.cbegin(), report.eegLowBetaRec.cend());
        res.eegHighBetaRec.assign(report.eegHighBetaRec.cbegin(), report.eegHighBetaRec.cend());
        res.eegQualityRec.assign(report.eegQualityRec.cbegin(), report.eegQualityRec.cend());
#endif
        return res;
    }


    PEPRReportRes getPEPRReport() {
        PEPRReportRes res;
#if PEPR_ALGORITHM
        auto report = peprDP.report();
        res.hrMax = report.hrMax;
        res.hrMin = report.hrMin;
        res.hrAvg = report.hrAvg;
        res.hrRec.assign(report.hrRec.cbegin(), report.hrRec.cend());
        res.rrRec.assign(report.rrRec.cbegin(), report.rrRec.cend());
        res.hrvRec.assign(report.hrvRec.cbegin(), report.hrvRec.cend());
        res.rrAvg = report.rrAvg;
        res.hrvAvg = report.hrvAvg;
        res.bcgQualityRec.assign(report.bcgQualityRec.cbegin(), report.bcgQualityRec.cend());
        res.rwQualityRec.assign(report.rwQualityRec.cbegin(), report.rwQualityRec.cend());
#endif
        return res;
    }


    SCEEGReportRes getSCEEGReport() {
        SCEEGReportRes res;
#if SCEEG_ALGORITHM
        auto report = sceegDP.report();
        res.eegAlphaRec.assign(report.eegAlphaRec.cbegin(), report.eegAlphaRec.cend());
        res.eegBetaRec.assign(report.eegBetaRec.cbegin(), report.eegBetaRec.cend());
        res.eegThetaRec.assign(report.eegThetaRec.cbegin(), report.eegThetaRec.cend());
        res.eegDeltaRec.assign(report.eegDeltaRec.cbegin(), report.eegDeltaRec.cend());
        res.eegGammaRec.assign(report.eegGammaRec.cbegin(), report.eegGammaRec.cend());
        res.eegQualityRec.assign(report.eegQualityRec.cbegin(), report.eegQualityRec.cend());
#endif
        return res;
    }


private:
#ifdef ATTENTION_ALGORITHM
#if ATTENTION_ALGORITHM
    ac::AttentionComputing attentionAC;
#endif
#endif
#ifdef RELAXATION_ALGORITHM
#if RELAXATION_ALGORITHM
    ac::RelaxationComputing relaxationAC;
#endif
#endif
#ifdef PRESSURE_ALGORITHM
#if PRESSURE_ALGORITHM
    ac::PressureComputing pressureAC;
#endif
#endif
#ifdef PLEASURE_ALGORITHM
#if PLEASURE_ALGORITHM
    ac::PleasureComputing pleasureAC;
#endif
#endif
#ifdef AROUSAL_ALGORITHM
#if AROUSAL_ALGORITHM
    ac::ArousalComputing arousalAC;
#endif
#endif
#ifdef COHERENCE_ALGORITHM
#if COHERENCE_ALGORITHM
    ac::CoherenceComputing coherenceAC;
#endif
#endif
#ifdef SLEEP_ALGORITHM
#if SLEEP_ALGORITHM
    ac::SleepComputing sleepAC;
#endif
#endif
#ifdef FLOW_ALGORITHM
#if FLOW_ALGORITHM
    ac::MeditationComputing flowAC;
#endif
#endif
#if EEG_ALGORITHM
    dp::EEGProgress eegDP;
#endif
#if SCEEG_ALGORITHM
    dp::SCEEGProcess sceegDP;
#endif
#if HR_ALGORITHM

    dp::HRProgress hrDP;
#endif
#if PEPR_ALGORITHM
    dp::PEPRProgress peprDP;
#endif

    SessionCache cache;
    dsp::DeviceInfoFtV1 device;
    std::vector<double> sleepEEGBuffer;
    double sleepDegreeTmp;
    int sleepStateTmp;
    int sleepStageTmp;
    double sleepSpindleTmp;
};

AffectiveAlgorithm::AffectiveAlgorithm() {
    ptr = new AffectiveAlgorithmImpl();
}


AffectiveAlgorithm::~AffectiveAlgorithm() {
    if (ptr) {
        delete ptr;
    }
}

ArousalReportRes AffectiveAlgorithm::getArousalReport() {
    //guard ptr is not nullpointer
    return ptr->getArousalReport();
}

CoherenceReportRes AffectiveAlgorithm::getCoherenceReport() {
    return ptr->getCoherenceReport();
}

PleasureReportRes AffectiveAlgorithm::getPleasureReport() {
    return ptr->getPleasureReport();
}

PressureReportRes AffectiveAlgorithm::getPressureReport() {
    return ptr->getPressureReport();
}

AttentionReportRes AffectiveAlgorithm::getAttentionReport() {
    return ptr->getAttentionReport();
}

RelaxationReportRes AffectiveAlgorithm::getRelaxationReport() {
    return ptr->getRelaxationReport();
}

SleepReportRes AffectiveAlgorithm::getSleepReport() {
    return ptr->getSleepReport();
}

FlowReportRes AffectiveAlgorithm::getFlowReport() {
    return ptr->getFlowReport();
}

HRReportRes AffectiveAlgorithm::getHRReport() {
    return ptr->getHRReport();
}

EEGReprotRes AffectiveAlgorithm::getEEGReport() {
    return ptr->getEEGReport();
}

PEPRReportRes AffectiveAlgorithm::getPEPRReport() {
    return ptr->getPEPRReport();
}

SCEEGReportRes AffectiveAlgorithm::getSCEEGReport() {
    return ptr->getSCEEGReport();
}

EEGAffectiveRes AffectiveAlgorithm::appendEEG(const std::vector<uint8_t> &eegRaw, bool isEar) {
#if EEG_ALGORITHM
    return ptr->appendEEG(eegRaw, isEar);
#endif
    EEGAffectiveRes res;
    return res;
}

SCEEGAffectiveRes AffectiveAlgorithm::appendSCEEG(const std::vector<uint8_t> &eegRaw) {
#if SCEEG_ALGORITHM
    return ptr->appendSCEEG(eegRaw);
#endif
    SCEEGAffectiveRes res;
    return res;
}

HRAffectiveRes AffectiveAlgorithm::appendHR(const std::vector<uint8_t> &hrRaw) {
#if HR_ALGORITHM
    return ptr->appendHR(hrRaw);
#endif
    HRAffectiveRes res{};
    return res;
}

PEPRAffectiveRes AffectiveAlgorithm::appendPEPR(const std::vector<uint8_t> &peprRaw) {
#if PEPR_ALGORITHM
    return ptr->appendPEPR(peprRaw);
#endif
    PEPRAffectiveRes res;
    return res;
}

int AffectiveAlgorithm::eegIndex() {
    return static_cast<int>(ptr->eegIndex);
}

int AffectiveAlgorithm::hrIndex() {
    return static_cast<int>(ptr->hrIndex);
}

int AffectiveAlgorithm::sleepLength() {
    return static_cast<int>(ptr->getSleepLength());
}

void AffectiveAlgorithm::setArousalEnable(bool enable) {
    ptr->AROURSL_ENABLE = enable;
}

void AffectiveAlgorithm::setCoherenceEnable(bool enable) {
    ptr->COHERENCE_ENABLE = enable;
}

void AffectiveAlgorithm::setPleasureEnable(bool enable) {
    ptr->PLEASURE_ENABLE = enable;
}

void AffectiveAlgorithm::setPressureEnable(bool enable) {
    ptr->PRESSURE_ENABLE = enable;
}

void AffectiveAlgorithm::setAttentionEnable(bool enable) {
    ptr->ATTENTION_ENABLE = enable;
}

void AffectiveAlgorithm::setRelaxationEnable(bool enable) {
    ptr->RELAXATION_ENABLE = enable;
}

void AffectiveAlgorithm::setSleepEnable(bool enable) {
    ptr->SLEEP_ENABLE = enable;
}

void AffectiveAlgorithm::setFlowEnable(bool enable) {
    ptr->FLOW_ENABLE = enable;
}

void AffectiveAlgorithm::setEEGEnable(bool enable) {
    ptr->EEG_ENABLE = enable;
}
void AffectiveAlgorithm::setHREnable(bool enable) {
    ptr->HR_ENABLE = enable;
}
void AffectiveAlgorithm::setPEPREnable(bool enable) {
    ptr->PEPR_ENABLE = enable;
}
void AffectiveAlgorithm::setSCEEGEnable(bool enable) {
    ptr->SCEEG_ENABLE = enable;
}

bool AffectiveAlgorithm::getArousalEnable() {
    return ptr->AROURSL_ENABLE;
}

bool AffectiveAlgorithm::getCoherenceEnable() {
    return ptr->COHERENCE_ENABLE;
}

bool AffectiveAlgorithm::getPleasureEnable() {
    return ptr->PLEASURE_ENABLE;
}

bool AffectiveAlgorithm::getPressureEnable() {
    return ptr->PRESSURE_ENABLE;
}

bool AffectiveAlgorithm::getAttentionEnable() {
    return ptr->ATTENTION_ENABLE;
}

bool AffectiveAlgorithm::getRelaxationEnable() {
    return ptr->RELAXATION_ENABLE;
}

bool AffectiveAlgorithm::getSleepEnable() {
    return ptr->SLEEP_ENABLE;
}

bool AffectiveAlgorithm::getFlowEnable() {
    return ptr->FLOW_ENABLE;
}

bool AffectiveAlgorithm::getEEGEnable() {
    return ptr->EEG_ENABLE;
}

bool AffectiveAlgorithm::getSCEEGEnable() {
    return ptr->SCEEG_ENABLE;
}

bool AffectiveAlgorithm::getHREnable() {
    return ptr->HR_ENABLE;
}

bool AffectiveAlgorithm::getPEPREnable() {
    return ptr->PEPR_ENABLE;
}

/********************csharp*****************/
#ifdef _WIN32
AffectiveAlgorithm * charpInit()
{
    return new AffectiveAlgorithm();
}

void charpDeinit(AffectiveAlgorithm * p)
{
    delete p;
}

void csharpAppendHR(AffectiveAlgorithm * p ,uint8_t* hrRaw, int rawLen, CSharpHRAffectiveRes * res)
{
    std::vector<uint8_t> hrVector;
    for (size_t i = 0; i < rawLen; i++)
    {
        hrVector.push_back(hrRaw[i]);
    }
    auto value = p->appendHR(hrVector);
    res->arousal = value.arousal;
    res->coherence = value.coherence;
    res->pressure = value.pressure;
    res->hr = value.hr.hr;
    res->hrv= value.hr.hrv;
}

void csharpAppendEEG(AffectiveAlgorithm * p ,uint8_t* eegRaw, int rawLen, CSharpEEGAffectiveRes * res, double* leftWave, double* rightWave)
{
    std::vector<uint8_t> eegVector;
    for (size_t i = 0; i < rawLen; i++)
    {
        eegVector.push_back(eegRaw[i]);
    }
    auto value = p->appendEEG(eegVector);
    res->eegAlphaPower = value.eeg.eegAlphaPower;
    res->eegBetaPower = value.eeg.eegBetaPower;
    res->eegDeltaPower = value.eeg.eegDeltaPower;
    res->eegThetaPower = value.eeg.eegThetaPower;
    res->eegGammaPower = value.eeg.eegGammaPower;
    res->eegQuality = value.eeg.eegQuality;
    res->pleasure = value.pleasure;
    res->relaxation = value.relaxation;
    res->sleepDegree = value.sleep.sleepDegree;
    res->sleepState = value.sleep.sleepState;
    for (size_t i = 0; i < value.eeg.eeglWave.size(); i++)
    {
        leftWave[i] = value.eeg.eeglWave.at(i);
    }
    for (size_t i = 0; i < value.eeg.eegrWave.size(); i++)
    {
        rightWave[i] = value.eeg.eegrWave.at(i);
    }

}

int csharpGetEEGLength(AffectiveAlgorithm * p)
{
    return p->eegIndex();
}

int csharpGetHRLength(AffectiveAlgorithm * p)
{
    return p->hrIndex();
}

int csharpGetSleepLength(AffectiveAlgorithm* p)
{
    return p->sleepLength();
}

int csharpGetEEGReport(AffectiveAlgorithm * p, double* alphaRec, double* betaRec, double* thetaRec, double* deltaRec, double* gammaRec)
{
    if (p->eegIndex() == 0) {
        return 0;
    }
    auto value = p->getEEGReport();
    for (size_t i = 0; i < value.eegAlphaRec.size(); i++)
    {
        alphaRec[i] = value.eegAlphaRec[i];
        betaRec[i] = value.eegBetaRec[i];
        thetaRec[i] = value.eegThetaRec[i];
        deltaRec[i] = value.eegDeltaRec[i];
        gammaRec[i] = value.eegGammaRec[i];
    }
    return static_cast<int>(value.eegAlphaRec.size());
    
}

int csharpGetHRReport(AffectiveAlgorithm * p, int* hrRec, double* hrvRec)
{
    if (p->hrIndex() == 0) {
        return 0;
    }
    auto value = p->getHRReport();
    for (size_t i = 0; i < value.hrRec.size(); i++)
    {
        hrRec[i] = value.hrRec[i];
    }

    for (size_t i = 0; i < value.hrvRec.size(); i++)
    {
        hrvRec[i] = value.hrvRec[i];
    }
    return value.hrRec.size();
}

int csharpGetSleepReport(AffectiveAlgorithm * p, double* sleepCurve, CSharpSleepReportRes * res)
{
    auto value = p->getSleepReport();
    res->awakeDuration = value.awakeDuration;
    res->deepDuration = value.deepDuration;
    res->lightDuration = value.lightDuration;
    res->sleepLatency = value.sleepLatency;
    res->sleepPoint = value.sleepPoint;
    
    for (size_t i = 0; i < value.sleepCurve.size(); i++)
    {
        sleepCurve[i] = value.sleepCurve.at(i);
    }
    return static_cast<int>(value.sleepCurve.size());

}

int csharpGetRelaxationReport(AffectiveAlgorithm * p, double* relaxationRec, CSharpRelaxationReportRes * res)
{
    auto value = p->getRelaxationReport();

    res->relaxationAvg = value.relaxationAvg;

    for (size_t i = 0; i < value.relaxationRec.size(); i++)
    {
        relaxationRec[i] = value.relaxationRec[i];
    }

    return static_cast<int>(value.relaxationRec.size());
}

int csharpGetAttentionReport(AffectiveAlgorithm * p, double* attentionRec, CSharpAttentionReportRes * res)
{
    auto value = p->getAttentionReport();

    res->attentionAvg = value.attentionAvg;

    for (size_t i = 0; i < value.attentionRec.size(); i++)
    {
        attentionRec[i] = value.attentionRec[i];
    }

    return static_cast<int>(value.attentionRec.size());
}

int csharpGetPressureReport(AffectiveAlgorithm * p, double* pressureRec, CSharpPressureReportRes * res)
{
    auto value = p->getPressureReport();

    res->pressureAvg = value.pressureAvg;
    for (size_t i = 0; i < value.pressureRec.size(); i++)
    {
        pressureRec[i] = value.pressureRec[i];
    }
    
    return static_cast<int>(value.pressureRec.size());
}

int csharpGetPleasureReport(AffectiveAlgorithm * p, double* pleasureRec, CSharpPleasureReportRes * res)
{
    auto value = p->getPleasureReport();

    res->pleasureAvg = value.pleasureAvg;

    for (size_t i = 0; i < value.pleasureRec.size(); i++)
    {
        pleasureRec[i] = value.pleasureRec[i];
    }
    
    return static_cast<int>(value.pleasureRec.size());
}

int csharpGetCoherenceReport(AffectiveAlgorithm * p, double* coherenceRec, CSharpCoherenceReportRes * res)
{
    auto value = p->getCoherenceReport();
    res->coherenceAvg = value.coherenceAvg;
    for (size_t i = 0; i < value.coherenceRec.size(); i++)
    {
        coherenceRec[i] = value.coherenceRec[i];
    }
    return static_cast<int>(value.coherenceRec.size());
}

int csharpGetArousalReport(AffectiveAlgorithm * p, double* arousalRec, CSharpArousalReportRes * res)
{
    auto value = p->getArousalReport();
    res->arousalAvg = value.arousalAvg;
    for (size_t i = 0; i < value.arousalRec.size(); i++)
    {
        arousalRec[i] = value.arousalRec[i];
    }
    return static_cast<int>(value.arousalRec.size());
}
#endif
