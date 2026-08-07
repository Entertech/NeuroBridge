#ifndef DP_EEG_HEADER_FILE_GUARD 
#define DP_EEG_HEADER_FILE_GUARD
#include "Data.h"
#include "EEGHandler.h"
#include "SessionCache.h"
#include "TypeDefine.h"
namespace dp
{
    struct EEGTriggerRes
    {
        vectord eeglWave;
        vectord eegrWave;

        double eegAlphaPower;
        double eegBetaPower;
        double eegThetaPower;
        double eegDeltaPower;
        double eegGammaPower;
        double eegLowBetaPower;
        double eegHighBetaPower;
        int eegQuality;
    };

    struct EEGReprotRes
    {
        vectord eegAlphaRec;
        vectord eegBetaRec;
        vectord eegThetaRec;
        vectord eegDeltaRec;
        vectord eegGammaRec;
        vectord eegLowBetaRec;
        vectord eegHighBetaRec;
        vectori eegQualityRec;
    };

    struct EEGTemp
    {
        int index;
        basic::dsp::eeghandler::EEGHandlerTemp eeglHandlerTmp;
        basic::dsp::eeghandler::EEGHandlerTemp eegrHandlerTmp;
        basic::dsp::EEGPower eeglPowerTmp;
        basic::dsp::EEGPower eegrPowerTmp;
        basic::dsp::EEGPower eeglFeaturePowerTmp;
        basic::dsp::EEGPower eegrFeaturePowerTmp;
        vectord eegAlphaRec;
        vectord eegBetaRec;
        vectord eegThetaRec;
        vectord eegDeltaRec;
        vectord eegGammaRec;
        vectord eegLowBetaRec;
        vectord eegHighBetaRec;
        vectori eegQualityRec;
    };

    class EEGProgress
    {
    public:
        EEGTriggerRes trigger(basic::SessionCache &cache, vectord &eeglData, vectord &eegrData, bool isEar = false);

        EEGReprotRes report();
        EEGProgress();
        ~EEGProgress();

    private:
        EEGTemp temp;
    };

} // namespace dp
#endif