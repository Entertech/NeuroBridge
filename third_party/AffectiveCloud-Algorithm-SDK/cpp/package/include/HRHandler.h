#ifndef C69025BE_154B_4DB7_9B31_9C82281B6FE1
#define C69025BE_154B_4DB7_9B31_9C82281B6FE1
#include "Data.h"
#include "Device.h"
#include "TypeDefine.h"
#include "NumCpp.hpp"
namespace basic
{
    namespace dsp
    {
        namespace hrhandler
        {
            struct HRHandlerTemp
            {
                nc::NdArray<double> hrData;
                int validCount;
                nc::NdArray<double> hrStore;
                nc::NdArray<double> intervalStore;
                nc::NdArray<double> hrWaveStore;
            };

            struct HRHandlerResult
            {
                HRQuality quality;
                double hr;
                double hrv;
                double interval;
                HRPower power;
                double syncCor;
            };
            HRHandlerResult handler(const vectori &hrRawData, double splitLen, DeviceInfo *deviceInfo,
                                    HRHandlerTemp &tmp, double scopeLim = 60, double validCheckLen = 12);
        }

    } // namespace dsp

} // namespace basic

#endif /* C69025BE_154B_4DB7_9B31_9C82281B6FE1 */
