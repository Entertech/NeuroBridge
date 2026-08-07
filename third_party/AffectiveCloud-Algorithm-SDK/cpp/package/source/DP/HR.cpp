#include "Device.h"
#include "Data.h"
#include "NumCpp.hpp"
#include "HR.h"

using namespace basic;
using namespace dsp;
using namespace hrhandler;
namespace dp
{
    
    HRProgress::HRProgress()
    {
        temp.index = 0;
        temp.hrHandlerTmp.validCount = 0;
        
    }

    HRProgress::~HRProgress()
    {
    }

    HRTriggerRes HRProgress::trigger(basic::SessionCache &cache,const vectori &hrData)
    {
        DeviceInfoFtV1 *deviceInfo = new DeviceInfoFtV1();
        
        auto hrRes = handler(
            hrData,
            4.0,
            deviceInfo,
            temp.hrHandlerTmp,
            60,
            12
        );
        temp.index += 1;

        temp.hrRec.push_back(hrRes.hr);
        temp.hrvRec.push_back(hrRes.hrv);

        cache.hr = hrRes.hr;
        cache.hrv = hrRes.hrv;
        cache.hrSyncCor = hrRes.syncCor;
        cache.hrQuality = hrRes.quality;
        cache.hrPower = hrRes.power;
        delete deviceInfo;
        HRTriggerRes res;
        res.hr = static_cast<int>(hrRes.hr);
        res.hrv = nc::round(hrRes.hrv,2);
        return res;
    }

    HRReportRes HRProgress::report()
    {
        HRReportRes res;
        if (temp.hrRec.size() == 0)
            return res;
        res.hrRec = temp.hrRec;
        auto hrvTempNdArray = nc::NdArray<double>(temp.hrvRec);
        auto vTemp = nc::round(hrvTempNdArray, 2).toStlVector();
        res.hrvRec.assign(vTemp.begin(),vTemp.end());

        return res;
    }

}